from fastapi import APIRouter, File, Form, UploadFile

from app.core.config import settings
from app.exceptions.handlers import FileTooLargeError, InvalidRequestError
from app.schemas.base import ApiResponse
from app.schemas.document import ParsedDocument
from app.services import file_storage_service, ocr_cache_service

router = APIRouter(prefix="/documents", tags=["documents"])


async def _resolve_content(
    file: UploadFile | None, file_path: str | None
) -> tuple[bytes, str, str | None, str | None]:
    if bool(file) == bool(file_path):
        raise InvalidRequestError("file 또는 filePath 중 하나만 지정해야 합니다")

    if file is not None:
        content = await file.read()
        if len(content) > settings.max_upload_size_bytes:
            raise FileTooLargeError(settings.max_upload_size_mb)
        return content, file.filename, file.content_type, None

    properties = await file_storage_service.get_file_properties(file_path)
    if properties.size > settings.max_upload_size_bytes:
        raise FileTooLargeError(settings.max_upload_size_mb)

    content = await file_storage_service.download_file(file_path)
    filename = file_path.rsplit("/", 1)[-1]
    return content, filename, None, file_path


@router.post("/parse-di", response_model=ApiResponse[ParsedDocument])
async def parse_document_with_document_intelligence(
    userId: str = Form(default=""),
    infId: str = Form(default=""),
    rqtKey: str = Form(default=""),
    file: UploadFile | None = File(default=None),
    filePath: str | None = Form(default=None),
) -> ApiResponse[ParsedDocument]:
    content, filename, content_type, existing_file_path = await _resolve_content(file, filePath)

    parsed_document = await ocr_cache_service.get_or_process(
        content=content,
        filename=filename,
        content_type=content_type,
        existing_file_path=existing_file_path,
    )

    return ApiResponse[ParsedDocument](statusCode=200, statusMsg="OK", result=parsed_document)
