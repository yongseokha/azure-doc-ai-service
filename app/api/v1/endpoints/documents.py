from fastapi import APIRouter, UploadFile

from app.exceptions.handlers import FileTooLargeError
from app.core.config import settings
from app.schemas.document import ParsedDocument
from app.services import document_intelligence_service

router = APIRouter(prefix="/documents", tags=["documents"])


async def _read_and_validate(file: UploadFile) -> bytes:
    content = await file.read()
    if len(content) > settings.max_upload_size_bytes:
        raise FileTooLargeError(settings.max_upload_size_mb)
    return content


@router.post("/parse-di", response_model=ParsedDocument)
async def parse_document_with_document_intelligence(file: UploadFile) -> ParsedDocument:
    content = await _read_and_validate(file)
    text = await document_intelligence_service.extract_markdown(content)

    return ParsedDocument(filename=file.filename, content=text, char_count=len(text))
