from io import BytesIO
from pathlib import Path

from docx import Document
from pypdf import PdfReader

from app.exceptions.handlers import DocumentParsingError, UnsupportedFileTypeError

SUPPORTED_EXTENSIONS = {".pdf", ".docx"}


def _parse_pdf(content: bytes) -> str:
    try:
        reader = PdfReader(BytesIO(content))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages).strip()
    except Exception as exc:
        raise DocumentParsingError(str(exc)) from exc


def _parse_docx(content: bytes) -> str:
    try:
        document = Document(BytesIO(content))
        paragraphs = [p.text for p in document.paragraphs if p.text]
        return "\n".join(paragraphs).strip()
    except Exception as exc:
        raise DocumentParsingError(str(exc)) from exc


def parse_document(filename: str, content: bytes) -> str:
    extension = Path(filename).suffix.lower()

    if extension == ".pdf":
        return _parse_pdf(content)
    if extension == ".docx":
        return _parse_docx(content)

    raise UnsupportedFileTypeError(filename)
