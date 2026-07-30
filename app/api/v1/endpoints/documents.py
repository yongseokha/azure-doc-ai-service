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


@router.post(
    "/parse-di",
    response_model=ApiResponse[ParsedDocument],
    summary="문서 OCR 처리 (캐시 지원)",
)
async def parse_document_with_document_intelligence(
    userId: str = Form(default="", description="사용자 사번", examples=["12345"]),
    infId: str = Form(default="", description="인터페이스 ID (API별 고정값)", examples=["DOC_PARSE_DI_V1"]),
    rqtKey: str = Form(
        default="", description="요청키 (클라이언트 생성 랜덤 키)", examples=["k3n9X2b7QsT1m8pZ..."]
    ),
    file: UploadFile | None = File(default=None, description="업로드할 문서 파일 (PDF, DOCX 등)"),
    filePath: str | None = Form(
        default=None,
        description="Azure File Storage 내 기존 파일 경로 (file과 동시 사용 불가)",
        examples=["uploads/2026-07/계약서.pdf"],
    ),
) -> ApiResponse[ParsedDocument]:
    """
    Document Intelligence로 문서를 OCR 처리합니다.

    - 같은 내용의 문서가 이미 처리된 적이 있으면 캐시된 결과를 그대로 반환합니다 (`cache_hit=true`).
    - `file`(직접 업로드) 또는 `filePath`(Azure File Storage 내 기존 경로) 중 정확히 하나만 지정해야 합니다.
    - 동일 문서가 이미 처리 중이면 재실행하지 않고 202를 반환하니, 잠시 후 다시 시도해주세요.
    """
    content, filename, content_type, existing_file_path = await _resolve_content(file, filePath)

    parsed_document = await ocr_cache_service.get_or_process(
        content=content,
        filename=filename,
        content_type=content_type,
        existing_file_path=existing_file_path,
    )

    return ApiResponse[ParsedDocument](statusCode=200, statusMsg="OK", result=parsed_document)
