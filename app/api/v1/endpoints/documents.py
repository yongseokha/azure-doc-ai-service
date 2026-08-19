from fastapi import APIRouter, BackgroundTasks, Response

from app.core.config import settings
from app.exceptions.handlers import DocumentNotFoundError, FileTooLargeError
from app.schemas.base import ApiResponse
from app.schemas.document import DocumentState, ParseDiRequest, ParsedDocument
from app.services import file_storage_service, ocr_cache_service

router = APIRouter(prefix="/documents", tags=["documents"])


async def _resolve_content(file_path: str) -> tuple[bytes, str, str | None]:
    properties = await file_storage_service.get_file_properties(file_path)
    if properties.size > settings.max_upload_size_bytes:
        raise FileTooLargeError(settings.max_upload_size_mb)

    content = await file_storage_service.download_file(file_path)
    filename = file_path.rsplit("/", 1)[-1]
    content_type = properties.content_settings.content_type if properties.content_settings else None
    return content, filename, content_type


@router.post(
    "/parse-di",
    response_model=ApiResponse[ParsedDocument],
    summary="문서 OCR 처리 (캐시 지원, 비동기)",
)
async def parse_document_with_document_intelligence(
    request: ParseDiRequest,
    background_tasks: BackgroundTasks,
    response: Response,
) -> ApiResponse[ParsedDocument]:
    """
    Document Intelligence로 문서를 OCR 처리합니다.

    - 같은 내용의 문서가 이미 처리된 적이 있으면 `200`과 함께 캐시된 결과를 즉시 반환합니다 (`cache_hit=true`, `status=completed`).
    - 새로 처리해야 하거나 이미 처리 중인 문서는 `202`와 `document_hash`를 즉시 반환하고,
      실제 OCR은 백그라운드에서 진행됩니다. `GET /documents/{document_hash}/status`로 진행 상태를 조회하세요.
    - `filePath`는 Azure File Storage에 이미 있는 문서의 경로여야 합니다.
    - 새로 처리를 시작한 경우, 완료 또는 실패 시 고정된 콜백 URL로 `termId`, `termHstSeq`, `fileDivCd`를
      받은 그대로 실어서 결과(`ocrResltKey`) 또는 오류(`ocrErrSbst`)를 전송합니다.
    """
    content, filename, content_type = await _resolve_content(request.filePath)

    cache_key, content_hash, result = await ocr_cache_service.claim(content, filename, content_type)

    if result is not None:
        result.userId = request.userId
        result.infId = request.infId
        result.rqtKey = request.rqtKey
        status_code = 200 if result.status == "completed" else 202
        status_msg = "OK" if result.status == "completed" else "문서가 처리 중입니다. 상태 조회 API로 확인해주세요."
        response.status_code = status_code
        return ApiResponse[ParsedDocument](statusCode=status_code, statusMsg=status_msg, result=result)

    background_tasks.add_task(
        ocr_cache_service.process_and_store,
        cache_key,
        content_hash,
        content,
        request.filePath,
        request.termId,
        request.termHstSeq,
        request.fileDivCd,
    )

    response.status_code = 202
    accepted = ParsedDocument(status="processing", cache_hit=False, document_hash=content_hash)
    accepted.userId = request.userId
    accepted.infId = request.infId
    accepted.rqtKey = request.rqtKey

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
