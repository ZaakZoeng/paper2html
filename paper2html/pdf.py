#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path

import fitz


DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)


def normalize(text):
    text = text.replace("\u00ad", "")
    text = re.sub(r"(?<=\w)-\n(?=\w)", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract(pdf_path):
    document = fitz.open(pdf_path)
    metadata = {k: (v or "").strip() for k, v in document.metadata.items()}
    pages = []
    for index, page in enumerate(document):
        text = normalize(page.get_text("text", sort=True))
        pages.append({"page": index + 1, "chars": len(text), "text": text})
    first_pages = "\n".join(page["text"] for page in pages[:3])
    doi_match = DOI_RE.search(" ".join([metadata.get("subject", ""), first_pages]))
    subject = metadata.get("subject", "")
    return {
        "source_pdf": str(pdf_path),
        "metadata": {
            "title": metadata.get("title", ""),
            "author": metadata.get("author", ""),
            "subject": subject,
            "journal_hint": subject.split(",", 1)[0].strip() if subject else "",
            "doi": doi_match.group(0).rstrip(".") if doi_match else "",
            "page_count": len(pages),
        },
        "quality": {
            "total_chars": sum(page["chars"] for page in pages),
            "low_text_pages": [page["page"] for page in pages if page["chars"] < 200],
            "ocr_recommended": sum(page["chars"] < 200 for page in pages) > len(pages) / 3,
        },
        "pages": pages,
    }


def main():
    parser = argparse.ArgumentParser(description="Extract page-grounded text from an academic PDF.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not args.pdf.is_file():
        parser.error("PDF not found: {}".format(args.pdf))
    result = extract(args.pdf.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Extracted {} pages to {}".format(result["metadata"]["page_count"], args.output))


if __name__ == "__main__":
    main()
