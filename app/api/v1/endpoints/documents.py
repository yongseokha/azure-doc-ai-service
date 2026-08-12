from fastapi import APIRouter, BackgroundTasks, File, Form, Response, UploadFile

from app.core.config import settings
from app.exceptions.handlers import DocumentNotFoundError, FileTooLargeError, InvalidRequestError
from app.schemas.base import ApiResponse
from app.schemas.document import DocumentState, ParsedDocument
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
    return content, filename, properties.content_settings.content_type, file_path


@router.post(
    "/parse-di",
    response_model=ApiResponse[ParsedDocument],
    summary="문서 OCR 처리 (캐시 지원, 비동기)",
)
async def parse_document_with_document_intelligence(
    background_tasks: BackgroundTasks,
    response: Response,
    userId: str = Form(default="", description="사용자 사번", examples=["12345"]),
    infId: str = Form(default="", description="인터페이스 ID (API별 고정값)", examples=["DOC_PARSE_DI_V1"]),
    rqtKey: str = Form(
        default="", description="요청키 (클라이언트 생성 랜덤 키)", examples=["k3n9X2b7QsT1m8pZ..."]
    ),
    termId: str = Form(default="", description="약관 ID", examples=["T12345"]),
    termHstSeq: str = Form(default="", description="약관 이력 순번", examples=["1"]),
    fileDivCd: str = Form(default="", description="파일 구분 코드", examples=["01"]),
    file: UploadFile | None = File(default=None, description="업로드할 문서 파일 (PDF, DOCX 등)"),
    filePath: str | None = Form(
        default=None,
        description="Azure File Storage 내 기존 파일 경로 (file과 동시 사용 불가)",
        examples=["uploads/2026-07/계약서.pdf"],
    ),
) -> ApiResponse[ParsedDocument]:
    """
    Document Intelligence로 문서를 OCR 처리합니다.

    - 같은 내용의 문서가 이미 처리된 적이 있으면 `200`과 함께 캐시된 결과를 즉시 반환합니다 (`cache_hit=true`, `status=completed`).
    - 새로 처리해야 하거나 이미 처리 중인 문서는 `202`와 `document_hash`를 즉시 반환하고,
      실제 OCR은 백그라운드에서 진행됩니다. `GET /documents/{document_hash}/status`로 진행 상태를 조회하세요.
    - `file`(직접 업로드) 또는 `filePath`(Azure File Storage 내 기존 경로) 중 정확히 하나만 지정해야 합니다.
    - 새로 처리를 시작한 경우, 완료 또는 실패 시 고정된 콜백 URL로 `termId`, `termHstSeq`, `fileDivCd`를
      받은 그대로 실어서 결과(`ocrResltKey`) 또는 오류(`ocrErrSbst`)를 전송합니다.
    """
    content, filename, content_type, existing_file_path = await _resolve_content(file, filePath)

    cache_key, content_hash, result = await ocr_cache_service.claim(content, filename, content_type)

    if result is not None:
        result.userId = userId
        result.infId = infId
        result.rqtKey = rqtKey
        status_code = 200 if result.status == "completed" else 202
        status_msg = "OK" if result.status == "completed" else "문서가 처리 중입니다. 상태 조회 API로 확인해주세요."
        response.status_code = status_code
        return ApiResponse[ParsedDocument](statusCode=status_code, statusMsg=status_msg, result=result)

    background_tasks.add_task(
        ocr_cache_service.process_and_store,
        cache_key,
        content_hash,
        filename,
        content,
        content_type,
        existing_file_path,
        termId,
        termHstSeq,
        fileDivCd,
    )

    response.status_code = 202
    accepted = ParsedDocument(status="processing", cache_hit=False, document_hash=content_hash)
    accepted.userId = userId
    accepted.infId = infId
    accepted.rqtKey = rqtKey

    return ApiResponse[ParsedDocument](
        statusCode=202, statusMsg="문서 처리를 접수했습니다. 상태 조회 API로 확인해주세요.", result=accepted
    )


@router.get(
    "/{document_hash}/status",
    response_model=ApiResponse[DocumentState],
    summary="문서 OCR 처리 상태 조회",
)
async def get_document_status(document_hash: str) -> ApiResponse[DocumentState]:
    """`document_hash`로 문서 OCR 처리 상태(`processing`/`completed`/`failed`)와, 완료 시 결과 경로를 조회합니다."""
    status_result = await ocr_cache_service.get_status(document_hash)
    if status_result is None:
        raise DocumentNotFoundError()

    return ApiResponse[DocumentState](statusCode=200, statusMsg="OK", result=status_result)
