"""Shared citation and naming rules for journal papers and identified preprints."""
import re
from datetime import datetime

ARXIV_ID = r'(?:\d{4}\.\d{4,5}|[a-z][a-z.-]*/\d{7})(?:v[1-9]\d*)?'


def is_preprint(metadata):
    return metadata.get('publication_type') == 'preprint'


def reference_url(metadata):
    identifier = metadata.get('preprint_id')
    if is_preprint(metadata) and metadata.get('preprint_server') == 'arXiv' and isinstance(identifier, str) and re.fullmatch(ARXIV_ID, identifier):
        return 'https://arxiv.org/abs/'+identifier
    doi = metadata.get('doi')
    if doi:
        doi = re.sub(r'^https?://(?:dx\.)?doi\.org/', '', str(doi).strip(), flags=re.I)
        if not re.fullmatch(r'10\.\d{4,9}/[^\s<>]+', doi):
            raise ValueError('doi must be a DOI or doi.org URL')
        return 'https://doi.org/'+doi
    raise ValueError('缺少可核实的 DOI 或预印本标识')


def date_component(metadata):
    date = metadata.get('publication_date')
    if not date and is_preprint(metadata):
        return 'undated'
    if not isinstance(date, str) or not re.fullmatch(r'\d{8}', date):
        raise ValueError('publication_date must be YYYYMMDD')
    datetime.strptime(date, '%Y%m%d')
    return date


def preprint_notice(metadata):
    date = metadata.get('publication_date')
    date_text = '当前版本发布日期：'+date[:4]+'-'+date[4:6]+'-'+date[6:] if date else '当前版本发布日期未确认，未推测日期'
    return '**预印本**：来源 '+metadata['preprint_server']+'；'+date_text+'。该日期不是正式期刊发表日期；预印本标识不代表已通过同行评审。'
