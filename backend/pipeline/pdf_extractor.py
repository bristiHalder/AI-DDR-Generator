"""
pdf_extractor.py  — Fast PDF text + image extraction using PyMuPDF.

Speed optimisations
───────────────────
• JPEG encoding instead of PNG  →  ~4-6× smaller base64, much faster encode
• Per-PDF image cap (MAX_IMAGES) →  prevents OOM / slow encode on large PDFs
• Minimum image size filter      →  skips logos, decorative icons, separators
• Lazy pixmap free               →  releases memory immediately after encode
"""

import fitz          # PyMuPDF
import base64
import re
from pathlib import Path
from typing import Any
from collections import Counter

# ── Limits ────────────────────────────────────────────────────────────────────
MAX_IMAGES_PER_PDF = 12   # was 30 -- caused 45s extraction; 12 is enough for report
MIN_IMG_WIDTH      = 150  # px -- skip small logos/icons
MIN_IMG_HEIGHT     = 150
JPEG_QUALITY       = 60   # lower = faster encode, smaller base64 payload


def extract_pdf(pdf_path: str) -> dict[str, Any]:
    """
    Extract text and images from a PDF.

    Returns
    -------
    {
        "source":       str,           # filename
        "total_pages":  int,
        "full_text":    str,           # cleaned, concatenated page text
        "pages":        [...],         # per-page text + image list
        "all_images":   [...],         # [{page, index, data_uri, width, height}]
    }
    """
    path = Path(pdf_path)
    doc  = fitz.open(str(path))

    pages_data      = []
    all_images      = []
    full_text_parts = []
    seen_xrefs      = set()   # de-duplicate repeated images (e.g. company logo)
    image_count     = 0

    for page_no, page in enumerate(doc, start=1):
        # ── Text ──────────────────────────────────────────────────────────
        text = _clean_text(page.get_text("text"))
        full_text_parts.append(f"[Page {page_no}]\n{text}")

        # ── Images ────────────────────────────────────────────────────────
        page_images = []

        if image_count < MAX_IMAGES_PER_PDF:
            for img_info in page.get_images(full=True):
                if image_count >= MAX_IMAGES_PER_PDF:
                    break

                xref = img_info[0]
                if xref in seen_xrefs:
                    continue              # skip duplicate (logo on every page)
                seen_xrefs.add(xref)

                try:
                    pix = fitz.Pixmap(doc, xref)

                    # Skip small images (icons / decorations)
                    if pix.width < MIN_IMG_WIDTH or pix.height < MIN_IMG_HEIGHT:
                        pix = None
                        continue

                    # Convert CMYK → RGB
                    if pix.n > 4:
                        pix = fitz.Pixmap(fitz.csRGB, pix)

                    # Encode as JPEG (much faster & smaller than PNG)
                    jpg_bytes  = pix.tobytes("jpeg", jpg_quality=JPEG_QUALITY)
                    b64        = base64.b64encode(jpg_bytes).decode("utf-8")
                    data_uri   = f"data:image/jpeg;base64,{b64}"

                    entry = {
                        "page":     page_no,
                        "index":    img_info[0],
                        "data_uri": data_uri,
                        "width":    pix.width,
                        "height":   pix.height,
                    }
                    page_images.append(entry)
                    all_images.append(entry)
                    image_count += 1
                    pix = None   # free ASAP

                except Exception:
                    continue

        pages_data.append({
            "page_no": page_no,
            "text":    text,
            "images":  page_images,
        })

    doc.close()

    return {
        "source":      path.name,
        "total_pages": len(pages_data),
        "full_text":   "\n\n".join(full_text_parts),
        "pages":       pages_data,
        "all_images":  all_images,
    }


def _clean_text(text: str) -> str:
    """
    Normalise extracted text to reduce token waste:
    - Unicode dashes / quotes → ASCII
    - Standalone page numbers stripped
    - 3+ blank lines collapsed to 1
    - Repeated header/footer lines removed
    """
    # ASCII normalisation
    text = (
        text
        .replace('\u2013', '-').replace('\u2014', '-')
        .replace('\u2019', "'")
        .replace('\u201c', '"').replace('\u201d', '"')
        .replace('\u00b0', ' deg')
        .replace('\u2103', ' degC')
    )

    # Strip standalone page numbers
    text = re.sub(r'^\s*\d{1,3}\s*$', '', text, flags=re.MULTILINE)

    # Collapse whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    lines = [l.rstrip() for l in text.splitlines()]

    # Remove lines that appear 3+ times (repeated header / footer boilerplate)
    counts = Counter(l.strip() for l in lines if len(l.strip()) > 4)
    repeated = {l for l, c in counts.items() if c >= 3}
    lines = [l for l in lines if l.strip() not in repeated]

    return "\n".join(lines).strip()


def get_significant_images(
    pdf_data: dict,
    min_width:  int = MIN_IMG_WIDTH,
    min_height: int = MIN_IMG_HEIGHT,
) -> list[dict]:
    """Return only meaningful images (already filtered during extraction)."""
    return [
        img for img in pdf_data["all_images"]
        if img.get("width", 0) >= min_width and img.get("height", 0) >= min_height
    ]
