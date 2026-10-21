#!/usr/bin/env python3
"""Build a self-contained illustrated paper folder from existing MD or content JSON plus PDF."""
import argparse
import hashlib
import json
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .extract_figures import extract, read_plan
from .figure_format import find_figures, public_caption, write_file
from .render_markdown import build_markdown, safe_component, validate_content
from .render_html import build as build_html
from .validate_html import validate_html
from .validate_markdown import validate as validate_markdown


def convert_markdown(source, figures):
    """Change figure formatting only; retain every caption and all other lines."""
    text = source.read_text(encoding='utf-8')
    entries = find_figures(text)
    if set(figures) != {f['id'] for f in entries}:
        raise ValueError('Extracted images must match the complete Markdown figure inventory')
    lines = text.splitlines(keepends=True)
    for item in reversed(entries):
        if item.get('pdf_page') and item['pdf_page'] != figures[item['id']]['pdf_page']:
            raise ValueError('PDF page does not match article: '+item['id'])
        image = '!['+item['id']+']('+figures[item['id']]['path']+')\n\n'
        begin = item.get('image_start', item['start'])
        lines[begin:item['end']] = [image + public_caption(item) + '\n']
    return ''.join(lines), entries


def stem_for(source, content):
    if content is not None:
        short = validate_content(content)
        m = content['metadata']
        stem = m['publication_date']+'_'+safe_component(m['journal_abbr'])+'_'+safe_component(short)
    else:
        stem = source.stem
    if not re.fullmatch(r'\d{8}_[A-Za-z0-9._-]+_[A-Za-z0-9._-]+', stem) or len(stem) > 180:
        raise ValueError('Paper directory must use YYYYMMDD_Journal_ShortName')
    datetime.strptime(stem[:8], '%Y%m%d')
    return stem


def bundle(source, pdf, root, plan_path=None, force=False):
    source, pdf, root = Path(source).resolve(), Path(pdf).resolve(), Path(root).resolve()
    if source.suffix.lower() not in ('.md', '.json'):
        raise ValueError('Input must be Markdown or article content JSON')
    content = json.loads(source.read_text(encoding='utf-8')) if source.suffix.lower()=='.json' else None
    stem = stem_for(source, content)
    reports = root/'reports';reports.mkdir(parents=True, exist_ok=True)
    final = reports/stem
    if final.is_symlink():
        raise ValueError('Refusing a symbolic-link article directory')
    if final.exists() and not force:
        raise FileExistsError('Article directory exists; review it before explicit --force: '+str(final))
    if final.exists():
        marker = final/'bundle.json'
        if not marker.is_file() or json.loads(marker.read_text()).get('format') != 'read4md-bundle-v1':
            raise ValueError('Refusing to replace an unrelated directory')
    if plan_path:
        recorded = json.loads(Path(plan_path).read_text(encoding='utf-8')).get('source_pdf_sha256')
        if recorded and recorded != hashlib.sha256(pdf.read_bytes()).hexdigest():
            raise ValueError('Extraction manifest belongs to a different PDF')
        plan = read_plan(plan_path)
    elif content is not None:
        from .figure_format import figure_id
        plan = {figure_id(f['source']): f for section in content['article']['sections'] for f in section.get('figures', [])}
    else:
        entries = find_figures(source.read_text(encoding='utf-8'))
        if entries and any(not f.get('pdf_page') for f in entries):
            # Clean Markdown keeps PDF pages in the sidecar, not visible captions.
            previous = source.parent/'figures.json'
            if not previous.exists():
                raise ValueError('Clean Markdown requires a figure plan or sibling figures.json')
            recorded = json.loads(previous.read_text(encoding='utf-8')).get('source_pdf_sha256')
            if recorded and recorded != hashlib.sha256(pdf.read_bytes()).hexdigest():
                raise ValueError('Sibling figure manifest belongs to a different PDF')
            plan = read_plan(previous)
        else:
            plan = {f['id']: f for f in entries}
    with tempfile.TemporaryDirectory(prefix='read4md-bundle-') as temporary:
        staging = Path(temporary)/stem;staging.mkdir()
        manifest = extract(pdf, staging, plan)
        if manifest['unresolved']:
            # Keep review material for the agent to choose verified regions before retrying.
            review = reports/'.reviews'/(stem+'-'+uuid.uuid4().hex[:8])
            review.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copytree(staging, review)
            write_file(review/'figures.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            raise ValueError('Figure regions need inspection. Review folder: '+str(review))
        manifest['source_pdf'] = str(pdf)
        if content is not None:
            rendered = build_markdown(content, manifest['figures'])
            write_file(staging/'content.json', json.dumps(content, ensure_ascii=False, indent=2))
            original_entries = [f for s in content['article']['sections'] for f in s.get('figures', [])]
        else:
            rendered, original_entries = convert_markdown(source, manifest['figures'])
        for item in original_entries:
            from .figure_format import figure_id
            key = figure_id(item['source'])
            if item.get('pdf_page') and item['pdf_page'] != manifest['figures'][key]['pdf_page']:
                raise ValueError('Extraction plan conflicts with the article PDF page: '+key)
            manifest['figures'][key]['source_label'] = item['source']
        write_file(staging/'figures.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        markdown = staging/(stem+'.md')
        write_file(markdown, rendered)
        md_result = validate_markdown(markdown)
        html_result = {}
        document, report = build_html(rendered, markdown)
        report['validation'] = validate_html(document, rendered)
        filename = stem+'.html'
        write_file(staging/filename, document)
        html_result[filename] = report
        provenance = {'input_file': str(source), 'input_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                      'source_pdf': str(pdf), 'source_pdf_sha256': manifest['source_pdf_sha256'],
                      'figures': original_entries,
                      'format_changes': ['Illustrations inserted', 'Figure panel range moved to provenance', 'PDF page and editorial labels removed from display'],
                      'semantic_review': 'Image regions and scientific content still require source review'}
        write_file(staging/'provenance.json', json.dumps(provenance, ensure_ascii=False, indent=2))
        write_file(staging/'validation.json', json.dumps({'markdown': md_result, 'html': html_result}, ensure_ascii=False, indent=2))
        files = {str(p.relative_to(staging)): hashlib.sha256(p.read_bytes()).hexdigest() for p in staging.rglob('*') if p.is_file()}
        write_file(staging/'bundle.json', json.dumps({'format': 'read4md-bundle-v1', 'paper': stem, 'files': files}, ensure_ascii=False, indent=2))
        # Copy to a staging sibling, then rename on the same filesystem only after all checks pass.
        import shutil
        pending = reports/('.'+stem+'-'+uuid.uuid4().hex)
        try:
            shutil.copytree(staging, pending)
            backup = None
            if final.exists():
                backup = reports/'.history'/(stem+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:6])
                backup.parent.mkdir(parents=True, exist_ok=True)
                final.rename(backup)
            try:
                pending.rename(final)
            except Exception:
                if backup and not final.exists(): backup.rename(final)
                raise
        finally:
            if pending.exists(): shutil.rmtree(pending)
    return {'directory': str(final), 'markdown': str(final/markdown.name), 'figures': len(manifest['figures']), 'html': list(html_result)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source', type=Path, help='Existing article MD or content JSON')
    ap.add_argument('--pdf', type=Path, required=True)
    ap.add_argument('--root', type=Path, default=Path.cwd())
    ap.add_argument('--plan', type=Path, help='Verified figure page/clip/region plan')
    ap.add_argument('--force', action='store_true', help='Archive an existing managed bundle before replacing it')
    args = ap.parse_args()
    print(json.dumps(bundle(args.source, args.pdf, args.root, args.plan, args.force), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
