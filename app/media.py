"""Optional PDF/OCR ingest. Missing dependency -> clear message, no crash."""

from io import BytesIO


def pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("PDF support needs: pip install pypdf") from exc
    reader = PdfReader(BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def image_text(data: bytes) -> str:
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Screenshot support needs: pip install Pillow pytesseract + tesseract binary") from exc
    return pytesseract.image_to_string(Image.open(BytesIO(data)))
