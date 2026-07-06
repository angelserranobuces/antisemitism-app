"""
PDF text extraction pipeline with three fallback methods:
  1. PyMuPDF direct text  — fastest, works on searchable PDFs
  2. pdfplumber           — alternative text extractor
  3. pytesseract OCR      — for scanned/image PDFs (requires tesseract)
"""

import io
import logging
from pathlib import Path
from typing import List, Dict, Tuple

logger = logging.getLogger(__name__)

RELEVANCE_KEYWORDS = [
    "israel", "israelí", "israelíes", "judío", "judía", "judíos",
    "sionista", "sionismo", "palestin", "gaza", "cisjordania",
    "hamas", "hezbollah", "iran", "irán", "antisemit",
    "flotilla", "genocidio", "apartheid", "ocupación", "colono",
    "netanyahu", "mossad", "oriente medio", "holocausto", "shoah",
    "terroris", "milici", "cohete", "misil", "rehén", "rehenes",
    "ataque", "ofensiva", "expansionis", "idf", "tsahal",
]

MIN_TEXT_CHARS = 300   # below this → try next method


def _is_relevant(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in RELEVANCE_KEYWORDS)


# ── Method 1: PyMuPDF direct text extraction ──────────────────────────────────

def _extract_fitz(file_bytes: bytes) -> List[Dict]:
    """Use PyMuPDF (fitz) text extraction — handles more PDF variants than pdfplumber."""
    import fitz  # pip3 install pymupdf
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    pages = []
    for i, page in enumerate(doc):
        text = page.get_text("text")  # plain text mode
        pages.append({"page_num": i + 1, "text": text, "is_relevant": _is_relevant(text)})
    doc.close()
    return pages


# ── Method 2: pdfplumber ──────────────────────────────────────────────────────

def _extract_pdfplumber(file_bytes: bytes) -> List[Dict]:
    import pdfplumber
    pages = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            pages.append({"page_num": i + 1, "text": text, "is_relevant": _is_relevant(text)})
    return pages


# ── Method 3: Tesseract OCR via PyMuPDF rendering ────────────────────────────

def _extract_ocr(file_bytes: bytes) -> List[Dict]:
    """
    Render each page to an image with PyMuPDF, then OCR with pytesseract.
    Requires: pip3 install pymupdf pytesseract pillow
              brew install tesseract tesseract-lang   (macOS)
           or apt install tesseract-ocr tesseract-ocr-spa  (Linux)
    """
    import fitz
    import pytesseract
    from PIL import Image

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    mat = fitz.Matrix(150 / 72, 150 / 72)  # 150 DPI — good balance for OCR accuracy
    pages = []

    for i, page in enumerate(doc):
        try:
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            text = pytesseract.image_to_string(img, lang="spa+eng",
                                               config="--psm 1 --oem 3")
            pages.append({"page_num": i + 1, "text": text,
                          "is_relevant": _is_relevant(text)})
        except Exception as e:
            logger.warning(f"OCR failed on page {i+1}: {e}")
            pages.append({"page_num": i + 1, "text": "", "is_relevant": False})

    doc.close()
    return pages


# ── Method 0: plain-text files ────────────────────────────────────────────────

def _extract_txt(file_bytes: bytes) -> List[Dict]:
    """Read a plain-text file and split into ~3000-char 'pages'."""
    text = file_bytes.decode("utf-8", errors="replace")
    chunk_size = 3000
    pages = []
    for i, start in enumerate(range(0, max(len(text), 1), chunk_size)):
        chunk = text[start:start + chunk_size]
        pages.append({"page_num": i + 1, "text": chunk, "is_relevant": _is_relevant(chunk)})
    return pages if pages else [{"page_num": 1, "text": text, "is_relevant": _is_relevant(text)}]


# ── Public entry point ────────────────────────────────────────────────────────

def extract_text_from_pdf(file_bytes: bytes, filename: str) -> Tuple[List[Dict], bool]:
    """
    Returns (pages_data, used_ocr).
    pages_data = [{"page_num": int, "text": str, "is_relevant": bool}]
    Accepts .txt (direct read) and .pdf (three-method fallback).
    """
    # .txt — just read directly
    if Path(filename).suffix.lower() == ".txt":
        pages = _extract_txt(file_bytes)
        total = sum(len(p["text"]) for p in pages)
        logger.info(f"{filename}: plain-text read → {total} chars")
        return pages, False

    # Method 1 — PyMuPDF direct
    try:
        pages = _extract_fitz(file_bytes)
        total = sum(len(p["text"]) for p in pages)
        if total >= MIN_TEXT_CHARS:
            logger.info(f"{filename}: fitz extraction → {total} chars")
            return pages, False
        logger.info(f"{filename}: fitz got {total} chars (too low) → trying pdfplumber")
    except Exception as e:
        logger.warning(f"{filename}: fitz failed: {e}")

    # Method 2 — pdfplumber
    try:
        pages = _extract_pdfplumber(file_bytes)
        total = sum(len(p["text"]) for p in pages)
        if total >= MIN_TEXT_CHARS:
            logger.info(f"{filename}: pdfplumber → {total} chars")
            return pages, False
        logger.info(f"{filename}: pdfplumber got {total} chars (too low) → trying OCR")
    except Exception as e:
        logger.warning(f"{filename}: pdfplumber failed: {e}")

    # Method 3 — Tesseract OCR (required for scanned/image PDFs)
    try:
        import pytesseract  # noqa — just check it's installed
        import fitz          # noqa
    except ImportError as ie:
        missing = "pytesseract" if "pytesseract" in str(ie) else "pymupdf"
        raise RuntimeError(
            f"This PDF is a scanned image and requires OCR.\n"
            f"Run these two commands in Terminal, then restart the server:\n\n"
            f"  brew install tesseract tesseract-lang\n"
            f"  pip3 install pytesseract\n\n"
            f"(Missing package: {missing})"
        )

    try:
        pages = _extract_ocr(file_bytes)
        total = sum(len(p["text"]) for p in pages)
        logger.info(f"{filename}: OCR → {total} chars")
        return pages, True
    except Exception as e:
        raise RuntimeError(f"OCR failed: {e}")


def build_analysis_context(pages_data: List[Dict], max_chars: int = 50000) -> str:
    """
    Build condensed context string. Prioritises relevant pages.
    """
    relevant = [p for p in pages_data if p["is_relevant"]]
    source = relevant if relevant else pages_data

    chunks, total = [], 0
    for p in source:
        text = p["text"].strip()
        if not text:
            continue
        chunk = f"\n--- PAGE {p['page_num']} ---\n{text}"
        if total + len(chunk) > max_chars:
            remaining = max_chars - total
            if remaining > 200:
                chunks.append(chunk[:remaining] + "\n…[truncated]")
            break
        chunks.append(chunk)
        total += len(chunk)

    return "\n".join(chunks)


# ── Filename parser ───────────────────────────────────────────────────────────

NEWSPAPER_ALIASES = {
    "el mundo":  "El Mundo",
    "elmundo":   "El Mundo",
    "el pais":   "El País",
    "el país":   "El País",
    "elpais":    "El País",
    "la razon":  "La Razón",
    "la razón":  "La Razón",
    "la-razon":  "La Razón",
    "larazon":   "La Razón",
    "abc":       "ABC",
}


def _normalise_newspaper(raw: str) -> str:
    key = raw.strip().lower().replace("_", " ").replace("-", " ")
    if key in NEWSPAPER_ALIASES:
        return NEWSPAPER_ALIASES[key]
    for alias, canonical in NEWSPAPER_ALIASES.items():
        if alias in key:
            return canonical
    raise ValueError(
        f"Unknown newspaper '{raw}'. Expected: El Mundo, La Razón, El País, ABC"
    )


def parse_filename(filename: str) -> Tuple[str, str]:
    """
    DD-MM-YY-Newspaper.pdf (or .txt)  →  ("YYYY-MM-DD", "El País")
    """
    name = Path(filename).stem
    parts = name.split("-", 3)
    if len(parts) < 4:
        raise ValueError(
            f"'{filename}' must follow DD-MM-YY-Newspaper.pdf format"
        )
    day, month, yr, newspaper_raw = parts
    newspaper = _normalise_newspaper(newspaper_raw)
    year = int(yr) + 2000 if int(yr) < 100 else int(yr)
    iso_date = f"{year:04d}-{int(month):02d}-{int(day):02d}"
    return iso_date, newspaper
