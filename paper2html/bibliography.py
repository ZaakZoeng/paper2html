"""Deterministic filename labels and explicitly sourced publication dates."""
import hashlib
import json
import re
import unicodedata
from datetime import datetime
from urllib.parse import quote
from urllib.request import Request, build_opener

from .render import safe_component
from .provider import NoRedirect


class MissingPublicationDate(ValueError):
    """A missing fact must not cause the LLM to invent it during repair retries."""


def normalized_doi(value):
    return re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', str(value).strip(), flags=re.I).rstrip('.,;。；').lower()


def identifier_text(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', value).casefold() if c.isalnum())


def filename_labels(metadata):
    """These are output labels, not bibliographic facts or official abbreviations."""
    generated = {}
    for field, base, fallback in (
        ('journal_abbr', metadata.get('journal', ''), 'Journal'),
        ('short_name', metadata.get('title', ''), 'Paper'),
    ):
        if isinstance(metadata.get(field), str) and safe_component(metadata[field]) and len(safe_component(metadata[field])) <= 80:
            continue
        base = base if isinstance(base, str) else ''
        ascii_base = unicodedata.normalize('NFKD', base).encode('ascii', 'ignore').decode('ascii')
        label = safe_component(ascii_base)
        digest = hashlib.sha256((normalized_doi(metadata.get('doi', '')) or base or fallback).encode('utf-8')).hexdigest()[:8]
        if field == 'short_name':
            label = (label[:32].rstrip('._-') or fallback) + '-' + digest
        elif not label:
            label = fallback+'-'+digest
        elif len(label) > 40:
            label = label[:30].rstrip('._-')+'-'+digest
        metadata[field] = label
        generated[field] = label
    return generated


MONTHS = {name.lower(): index for index, name in enumerate(
    ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'), 1)}
for name, index in list(MONTHS.items()):
    MONTHS[name[:3]] = index


def parse_date(text):
    text = re.sub(r'\s+', ' ', text).strip()
    match = re.fullmatch(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', text)
    if match:
        year, month, day = map(int, match.groups())
    else:
        match = re.fullmatch(r'(\d{1,2})\s+([A-Za-z]+)\.?\s+(\d{4})', text)
        if match:
            day, month, year = int(match[1]), MONTHS.get(match[2].lower()), int(match[3])
        else:
            match = re.fullmatch(r'([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})', text)
            if not match: return None
            month, day, year = MONTHS.get(match[1].lower()), int(match[2]), int(match[3])
    try:
        return datetime(year, month, day).strftime('%Y%m%d')
    except (TypeError, ValueError):
        return None


def pdf_publication_dates(pages):
    date = r'(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}\s+[A-Za-z]+\.?\s+\d{4}|[A-Za-z]+\.?\s+\d{1,2},?\s+\d{4})'
    pattern = re.compile(r'(?:^|[;\n])\s*(?:first\s+published(?:\s+online)?|published(?:\s+online)?|publication\s+date|available\s+online)\s*[:：]?\s*(?:on\s+)?('+date+r')', re.I)
    found = []
    for page, text in pages.items():
        for match in pattern.finditer(text):
            value = parse_date(match[1])
            if value:
                found.append({'date': value, 'pdf_page': page, 'quote': match[0].strip()})
    return found


def crossref_publication_date(metadata, timeout=15):
    doi = normalized_doi(metadata.get('doi', ''))
    if not re.fullmatch(r'10\.\d{4,9}/[^\s<>]+', doi):
        raise ValueError('DOI 无效，不能查询出版日期')
    url = 'https://api.crossref.org/works/'+quote(doi, safe='/')
    request = Request(url, headers={'Accept': 'application/json', 'User-Agent': 'Paper2HTML (publication metadata lookup)'})
    with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
        raw = response.read(2*1024*1024+1)
    if len(raw) > 2*1024*1024:
        raise ValueError('Crossref 响应超过大小限制')
    response = json.loads(raw)
    record = response.get('message') if isinstance(response, dict) else None
    if not isinstance(record, dict) or normalized_doi(record.get('DOI', '')) != doi:
        raise ValueError('Crossref 返回的 DOI 不匹配')
    titles, journals = record.get('title', []), record.get('container-title', [])
    if not isinstance(titles, list) or not any(isinstance(t, str) and identifier_text(t) == identifier_text(metadata['title']) for t in titles):
        raise ValueError('Crossref 返回的论文标题不匹配，需要人工确认')
    if not isinstance(journals, list) or not any(isinstance(j, str) and identifier_text(j) == identifier_text(metadata['journal']) for j in journals):
        raise ValueError('Crossref 返回的期刊不匹配，需要人工确认')
    for field in ('published-online', 'published-print', 'published'):
        value = record.get(field)
        parts = value.get('date-parts') if isinstance(value, dict) else None
        if not isinstance(parts, list) or not parts or not isinstance(parts[0], list) or len(parts[0]) != 3:
            continue
        if any(type(n) is not int for n in parts[0]): continue
        try:
            date = datetime(*parts[0]).strftime('%Y%m%d')
        except ValueError:
            continue
        return {'date': date, 'source': 'Crossref', 'url': url, 'field': field, 'doi': doi,
                'title': titles[0], 'journal': journals[0]}
    raise ValueError('Crossref 未提供完整的年、月、日；不能用登记时间或接受日期代替')


def select_metadata_pages(pages, limit=36000):
    """Reserve context for explicit publication markers, including dates on late pages."""
    by_page = {p['page']: p['text'] for p in pages}
    selected, size, used = [], 0, set()
    for candidate in pdf_publication_dates(by_page):
        page = candidate['pdf_page']
        if page in used: continue
        text = by_page[page]
        offset = text.find(candidate['quote'])
        excerpt = text[max(0, offset-300):offset+len(candidate['quote'])+500]
        if size+len(excerpt) <= limit:
            selected.append({'page': page, 'text': excerpt})
            size += len(excerpt)
            used.add(page)
    for p in pages[:3]:
        if size+len(p['text']) <= limit:
            selected.append(p)
            size += len(p['text'])
    return selected


def complete_publication_date(result, pages, allow_lookup=True, lookup=None):
    metadata = result['metadata']
    if metadata.get('publication_date'):
        return
    candidates = pdf_publication_dates(pages)
    dates = {item['date'] for item in candidates}
    if len(dates) > 1:
        raise MissingPublicationDate('PDF 存在多个不同的正式发表日期，请在补充出版信息中确认 publication_date')
    if candidates:
        source = {'source': 'PDF', **candidates[0]}
        evidence = result.setdefault('metadata_evidence', [])
        evidence.append({'field': 'publication_date', 'pages': [source['pdf_page']], 'quote': source['quote']})
    else:
        if not allow_lookup:
            raise MissingPublicationDate('PDF 全文未找到正式发表日期，DOI 查询已关闭；请补充 publication_date（YYYYMMDD），不能使用收稿或接受日期')
        try:
            source = (lookup or crossref_publication_date)(metadata)
        except (OSError, ValueError, KeyError, TypeError):
            raise MissingPublicationDate('PDF 全文未找到正式发表日期，Crossref 查询不可用、信息不完整或记录不匹配；请补充 publication_date（YYYYMMDD），无需补充文件命名标签') from None
    metadata['publication_date'] = source['date']
    result['publication_source'] = source
    result['date_basis'] = ('PDF 正式发表日期' if source['source'] == 'PDF' else 'Crossref '+source['field']+'；外部登记记录，不属于 PDF 原文证据')
