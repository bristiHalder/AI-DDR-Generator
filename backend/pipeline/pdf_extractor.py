"""
pdf_extractor.py
Extracts text content and images from PDF files.
Returns a structured dict with per-page text and base64-encoded images.

Performance limits:
- Max 30 images extracted per PDF (prevents memory issues on large files)
- Images downscaled to max 800px on longest side
- Text cleaned aggressively to reduce token usage
"""

import fitz  # PyMuPDF
import base64
import re
from collections import Counter
from pathlib import Path
from typing import Any

# ── Limits ────────────────────────────────────────────────────────────────────
MAX_IMAGES_PER_PDF = 30     # Hard cap — prevents memory exhaustion on image-heavy PDFs
MAX_IMAGE_DIMENSION = 800   # Downscale anything larger than this (px)
MIN_IMAGE_DIMENSION = 80    # Skip tiny images (icons, logos, bullets)


def extract_pdf(pdf_path: str) -> dict[str, Any]:
    """
    Extract text and images from a PDF file.

    Returns:
        {
            "source": filename,
            "total_pages": int,
            "full_text": str,               # all page text concatenated (cleaned)
            "pages": [
                {
                    "page_no": int,
                    "text": str,
                    "images": [{"index": int, "data_uri": "data:image/png;base64,..."}]
                }
            ],
            "all_images": [{"page": int, "index": int, "data_uri": str, "width": int, "height": int}]
        }
    """
    path = Path(pdf_path)
    doc = fitz.open(str(path))

    pages_data = []
    all_images = []
    full_text_parts = []
    total_images_extracted = 0

    # Track already-seen image xrefs to avoid duplicates across pages
    seen_xrefs: set[int] = set()

    for page_no, page in enumerate(doc, start=1):
        # ── Text extraction ───────────────────────────────────────────────────
        text = page.get_text("text")
        text = _clean_text(text)
        full_text_parts.append(f"[Page {page_no}]\n{text}")

        # ── Image extraction ──────────────────────────────────────────────────
        page_images = []

        if total_images_extracted < MAX_IMAGES_PER_PDF:
            image_list = page.get_images(full=True)

            for img_idx, img_info in enumerate(image_list):
                if total_images_extracted >= MAX_IMAGES_PER_PDF:
                    break

                xref = img_info[0]
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)

                try:
                    pix = fitz.Pixmap(doc, xref)

                    # Skip tiny images (icons, bullets, watermarks)
                    if pix.width < MIN_IMAGE_DIMENSION or pix.height < MIN_IMAGE_DIMENSION:
                        pix = None
                        continue

                    # Convert CMYK/other color spaces → RGB
                    if pix.n > 4:
                        pix = fitz.Pixmap(fitz.csRGB, pix)

                    # Downscale large images to save memory & HTML size
                    if max(pix.width, pix.height) > MAX_IMAGE_DIMENSION:
                        scale = MAX_IMAGE_DIMENSION / max(pix.width, pix.height)
                        new_w = int(pix.width * scale)
                        new_h = int(pix.height * scale)
                        mat = fitz.Matrix(scale, scale)
                        pix = pix.warp(fitz.Quad(pix.irect), new_w, new_h) if hasattr(pix, 'warp') else pix

                    png_bytes = pix.tobytes("png")
                    b64 = base64.b64encode(png_bytes).decode("utf-8")
                    data_uri = f"data:image/png;base64,{b64}"

                    entry = {
                        "page": page_no,
                        "index": img_idx,
                        "data_uri": data_uri,
                        "width": pix.width,
                        "height": pix.height,
                    }
                    page_images.append(entry)
                    all_images.append(entry)
                    total_images_extracted += 1
                    pix = None  # free memory immediately

                except Exception as exc:
                    print(f"[pdf_extractor] Skipping image xref={xref} on page {page_no}: {exc}")
                    continue

        pages_data.append({
            "page_no": page_no,
            "text": text,
            "images": page_images,
        })

    doc.close()

    print(
        f"[pdf_extractor] {path.name}: {len(pages_data)} pages, "
        f"{len(all_images)} images, {len(''.join(full_text_parts))} text chars"
    )

    return {
        "source": path.name,
        "total_pages": len(pages_data),
        "full_text": "\n\n".join(full_text_parts),
        "pages": pages_data,
        "all_images": all_images,
    }


def _clean_text(text: str) -> str:
    """
    Clean and normalize extracted PDF text to reduce token usage.
    - Removes standalone page numbers
    - Strips repeated header/footer lines (appear 3+ times = likely boilerplate)
    - Normalises unicode punctuation
    - Collapses whitespace
    """
    # Normalize unicode punctuation
    text = (
        text.replace('\u2013', '-')
            .replace('\u2014', '-')
            .replace('\u2019', "'")
            .replace('\u201c', '"')
            .replace('\u201d', '"')
            .replace('\u2022', '-')   # bullet → dash
            .replace('\u00b0', '°')
    )
    # Remove standalone page numbers on their own line
    text = re.sub(r'^\s*\d{1,3}\s*$', '', text, flags=re.MULTILINE)
    # Collapse 3+ consecutive blank lines → 1
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Strip trailing whitespace per line
    lines = [line.rstrip() for line in text.splitlines()]
    # Remove lines that repeat 3+ times (likely headers/footers)
    line_counts = Counter(l.strip() for l in lines if len(l.strip()) > 4)
    repeated = {l for l, c in line_counts.items() if c >= 3}
    lines = [l for l in lines if l.strip() not in repeated]
    return "\n".join(lines).strip()


def get_significant_images(pdf_data: dict, min_width: int = 80, min_height: int = 80) -> list[dict]:
    """
    Return only meaningful inspection photos (not tiny logos/icons).
    """
    return [
        img for img in pdf_data["all_images"]
        if img.get("width", 0) >= min_width and img.get("height", 0) >= min_height
    ]
