#!/usr/bin/env python3
"""Extract original main figures from a PDF using captions and an optional region plan."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import fitz
from .figure_format import figure_id, write_file


def caption_candidates(document):
    """Only caption blocks beginning Figure N., never in-paragraph citations."""
    import re
    candidates = {}
    for page_no, page in enumerate(document, 1):
        for block in page.get_text('blocks'):
            if block[6] != 0:
                continue
            text = ' '.join(block[4].split())
            match = re.match(r'^(?:Figure|Fig\.?)\s+([1-9]\d*)\s*[.:|]\s*', text, re.I)
            if match:
                candidates.setdefault('Figure ' + match[1], []).append({'pdf_page': page_no, 'caption_bbox': list(block[:4]), 'caption_quote': text})
    return candidates


def read_plan(path):
    if not path:
        return None
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if 'article' in data:
        rows = [f for s in data['article']['sections'] for f in s.get('figures', [])]
    else:
        rows = data.get('figures', [])
        if isinstance(rows, dict):
            rows = [dict(value, source=key) for key, value in rows.items()]
    output = {}
    for item in rows:
        key = figure_id(item['source'])
        if key in output:
            raise ValueError('Duplicate figure in extraction plan: ' + key)
        output[key] = item
    return output


def valid_rect(values, page):
    if not isinstance(values, (list, tuple)) or len(values) != 4 or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise ValueError('clip must contain four finite PDF coordinates')
    rect = fitz.Rect(values)
    if rect.is_empty or not page.rect.contains(rect):
        raise ValueError('Figure clip is empty or outside the PDF page')
    return rect


def raster_bytes(document, info):
    # Preserve soft transparency masks; otherwise retain the original image bytes.
    xref = info['xref']
    smask = next((x[1] for page in document for x in page.get_images() if x[0] == xref), 0)
    if smask:
        pix = fitz.Pixmap(fitz.Pixmap(document, xref), fitz.Pixmap(document, smask))
        return pix.tobytes('png'), 'png'
    extracted = document.extract_image(xref)
    ext = extracted['ext']
    if ext in ('png', 'jpeg', 'jpg', 'webp'):
        return extracted['image'], ext
    return fitz.Pixmap(document, xref).tobytes('png'), 'png'


def extract(pdf, output_dir, plan=None, dpi=180):
    pdf, output_dir = Path(pdf).resolve(), Path(output_dir)
    if not 72 <= dpi <= 300:
        raise ValueError('dpi must be 72–300')
    document = fitz.open(pdf)
    if document.needs_pass:
        raise ValueError('Encrypted PDF requires an accessible source')
    candidates = caption_candidates(document)
    if plan is None:
        plan = {}
        for key, matches in candidates.items():
            plan[key] = {'source': key, 'pdf_page': matches[0]['pdf_page']} if len(matches) == 1 else {'source': key, 'reason': 'Multiple caption candidates; inspect and specify pdf_page'}
    result = {'version': 2, 'source_pdf': str(pdf), 'source_pdf_sha256': hashlib.sha256(pdf.read_bytes()).hexdigest(), 'figures': {}, 'unresolved': []}
    for key, item in sorted(plan.items(), key=lambda pair: int(pair[0].split()[1])):
        page_no = item.get('pdf_page')
        if type(page_no) is not int or not 1 <= page_no <= len(document):
            result['unresolved'].append({'source': key, 'reason': item.get('reason', 'A valid pdf_page is required')})
            continue
        page = document[page_no-1]
        record = {'pdf_page': page_no, 'source_label': item.get('source', key)}
        matches = [x for x in candidates.get(key, []) if x['pdf_page'] == page_no]
        if matches:
            record.update({k: v for k, v in matches[0].items() if k != 'pdf_page'})
        regions = item.get('regions')
        if item.get('clip') is not None:
            regions = [{'pdf_page': page_no, 'clip': item['clip']}]
        if regions:
            if not isinstance(regions, list) or len(regions) > 8:
                raise ValueError('regions must be a list of at most eight page clips')
            pixels, provenance = [], []
            for region in regions:
                number = region.get('pdf_page')
                if type(number) is not int or not 1 <= number <= len(document):
                    raise ValueError('Invalid region page')
                p = document[number-1]
                rect = valid_rect(region.get('clip'), p)
                pixels.append(p.get_pixmap(clip=rect, dpi=dpi, alpha=False))
                provenance.append({'pdf_page': number, 'clip': list(rect)})
            if page_no not in [region['pdf_page'] for region in regions]:
                raise ValueError('The extraction regions must include the primary figure page')
            if len(pixels) == 1:
                payload = pixels[0].tobytes('png')
            else:
                # Concatenate verified original regions vertically without changing panel content.
                contact = fitz.open()
                width = max(p.width for p in pixels)
                height = sum(p.height for p in pixels) + 16*(len(pixels)-1)
                if width*height > 60_000_000:
                    raise ValueError('Combined figure is too large; lower dpi')
                sheet = contact.new_page(width=width, height=height)
                top = 0
                for pix in pixels:
                    sheet.insert_image(fitz.Rect(0, top, pix.width, top+pix.height), pixmap=pix)
                    top += pix.height+16
                payload = sheet.get_pixmap().tobytes('png')
                contact.close()
            ext = 'png'
            record.update(method='verified-regions', regions=provenance, dpi=dpi)
        else:
            images = [x for x in page.get_image_info(xrefs=True) if x['xref'] and fitz.Rect(x['bbox']).get_area() >= page.rect.get_area()*0.01]
            xref = item.get('xref')
            if xref is not None:
                images = [x for x in images if x['xref'] == xref]
                safe = len(images) == 1
            else:
                # Strict fast path: one substantial raster, no vector/panel/text content outside it in the figure area.
                safe = len(images) == 1 and images[0]['width'] >= 300 and images[0]['height'] >= 200 and fitz.Rect(images[0]['bbox']).get_area() >= page.rect.get_area()*0.10 and len(matches) == 1 and sum(x['pdf_page'] == page_no for group in candidates.values() for x in group) == 1
                if safe:
                    rect = fitz.Rect(images[0]['bbox'])
                    caption_box = fitz.Rect(matches[0]['caption_bbox'])
                    # The caption must be below the raster or alongside it (column layout).
                    safe = caption_box.y0 >= rect.y1-4 or caption_box.x0 >= rect.x1-4
                    # Additional drawings next to the raster may be missing vector panels.
                    for drawing in page.get_drawings():
                        area = drawing['rect']
                        if area.get_area() > 100 and area.y0 >= rect.y0-5 and area.y1 <= rect.y1+5 and not rect.contains(area):
                            safe = False
                    # Uncaptured panel labels/text in the same vertical band make it ambiguous.
                    for block in page.get_text('blocks'):
                        area = fitz.Rect(block[:4])
                        if block[6] == 0 and block[4].strip() and area.y0 >= rect.y0-4 and area.y1 <= rect.y1+4 and not rect.contains(area) and not caption_box.intersects(area) and not (caption_box.x0 >= rect.x1-4 and area.x0 >= caption_box.x0-4):
                            safe = False
            if not safe:
                preview = output_dir/'assets'/'review'/('figure-'+key.split()[1]+'-page-'+str(page_no)+'.png')
                write_file(preview, page.get_pixmap(dpi=100, alpha=False).tobytes('png'))
                result['unresolved'].append({'source': key, 'pdf_page': page_no, 'reason': 'Ambiguous/vector/composite figure: inspect preview and specify clip, regions or verified xref', 'preview': str(preview.relative_to(output_dir))})
                continue
            info = images[0]
            payload, ext = raster_bytes(document, info)
            record.update(method='verified-xref' if xref is not None else 'caption-single-raster', xref=info['xref'], bbox=list(info['bbox']))
        relative = Path('assets')/('figure-'+key.split()[1]+'.'+ext)
        write_file(output_dir/relative, payload)
        record.update(path=relative.as_posix(), sha256=hashlib.sha256(payload).hexdigest())
        result['figures'][key] = record
    document.close()
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('pdf', type=Path)
    ap.add_argument('--output-dir', required=True, type=Path)
    ap.add_argument('--plan', type=Path, help='Content JSON or {figures:[{source,pdf_page,clip/regions/xref}]}')
    ap.add_argument('--dpi', type=int, default=180)
    args = ap.parse_args()
    result = extract(args.pdf, args.output_dir, read_plan(args.plan), args.dpi)
    write_file(args.output_dir/'figures.json', json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'figures': len(result['figures']), 'unresolved': result['unresolved'], 'manifest': str(args.output_dir/'figures.json')}, ensure_ascii=False, indent=2))
    if result['unresolved']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
