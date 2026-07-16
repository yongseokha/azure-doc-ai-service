from fastapi import APIRouter, Form, UploadFile

from app.exceptions.handlers import FileTooLargeError
from app.core.config import settings
from app.schemas.document import ParsedDocument, SummarizeResult
from app.services import azure_openai_service, document_intelligence_service, document_parser_service

router = APIRouter(prefix="/documents", tags=["documents"])


async def _read_and_validate(file: UploadFile) -> bytes:
    content = await file.read()
    if len(content) > settings.max_upload_size_bytes:
        raise FileTooLargeError(settings.max_upload_size_mb)
    return content


@router.post("/parse", response_model=ParsedDocument)
async def parse_document(file: UploadFile) -> ParsedDocument:
    content = await _read_and_validate(file)
    text = document_parser_service.parse_document(file.filename, content)

    return ParsedDocument(filename=file.filename, content=text, char_count=len(text))


@router.post("/parse-di", response_model=ParsedDocument)
async def parse_document_with_document_intelligence(file: UploadFile) -> ParsedDocument:
    content = await _read_and_validate(file)
    text = document_intelligence_service.extract_markdown(content)

    return ParsedDocument(filename=file.filename, content=text, char_count=len(text))


@router.post("/summarize", response_model=SummarizeResult)
async def summarize_document(
    file: UploadFile,
    instruction: str = Form(default="다음 문서를 핵심 위주로 요약해줘."),
    max_output_tokens: int = Form(default=800),
) -> SummarizeResult:
    content = await _read_and_validate(file)
    text = document_parser_service.parse_document(file.filename, content)

    summary = azure_openai_service.summarize_text(
        text=text,
        instruction=instruction,
        max_output_tokens=max_output_tokens,
    )

    return SummarizeResult(filename=file.filename, summary=summary, char_count=len(text))
