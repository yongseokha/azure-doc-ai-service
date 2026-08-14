import json

from fastapi import APIRouter, BackgroundTasks, Response

from app.exceptions.handlers import DocumentNotFoundError, InvalidRequestError
from app.schemas.base import ApiResponse
from app.schemas.terms import TermsVerificationRequest
from app.services import file_storage_service, terms_job_service, terms_verification_service

router = APIRouter(prefix="/terms", tags=["terms"])


@router.post(
    "/verify",
    response_model=ApiResponse[None],
    summary="약관 검증 요청 접수 (결과는 콜백으로 전달)",
)
async def verify_terms(
    request: TermsVerificationRequest,
    background_tasks: BackgroundTasks,
    response: Response,
) -> ApiResponse[None]:
    """
    상품별 항목 데이터를 약관 원문과 대조해 검증합니다.

    - 처리는 비동기로 이루어지며, 이 엔드포인트는 즉시 202를 반환합니다.
    - 완료되면 고정된 콜백 URL로 결과를 POST합니다. `knwlgInfoId`/`termVrfSeq`는 받은 그대로 콜백 본문에 실립니다.
    - 각 상품(`data[].name`)의 항목들은 `termInfo`에 있는 모든 문서와 교차 비교됩니다.
    - 항목 하나라도 처리에 실패하면 전체 요청이 실패로 처리되고, 실패 콜백이 전송됩니다.
    - 같은 `rqtKey`로 이미 처리 중인 요청이 있으면 재처리하지 않고, 완료된 요청이면 저장된 결과를 다시 콜백으로 보냅니다.
    """
    job = await terms_job_service.get_job(request.rqtKey)

    if job is not None and job["status"] == "processing" and not terms_job_service.is_stale(job["updated_at"]):
        response.status_code = 202
        return ApiResponse[None](statusCode=202, statusMsg="이미 처리 중입니다.", result=None)

    if job is not None and job["status"] == "completed":
        background_tasks.add_task(terms_verification_service.resend_stored_result, job)
        response.status_code = 202
        return ApiResponse[None](
            statusCode=202, statusMsg="이미 완료된 요청입니다. 저장된 결과를 다시 전송합니다.", result=None
        )

    await terms_job_service.claim_job(request)
    background_tasks.add_task(terms_verification_service.process_and_callback, request)

    response.status_code = 202
    return ApiResponse[None](statusCode=202, statusMsg="약관 검증 요청을 접수했습니다.", result=None)


@router.get(
    "/verify/{rqtKey}",
    response_model=ApiResponse[dict],
    summary="약관 검증 job 상태/결과 조회",
)
async def get_verification_status(rqtKey: str) -> ApiResponse[dict]:
    job = await terms_job_service.get_job(rqtKey)
    if job is None:
        raise DocumentNotFoundError()

    if job["status"] == "completed":
        content = await file_storage_service.download_file(job["result_file_path"])
        return ApiResponse[dict](statusCode=200, statusMsg="OK", result=json.loads(content))

    return ApiResponse[dict](
        statusCode=200,
        statusMsg="OK",
        result={"status": job["status"], "errorMessage": job.get("error_message")},
    )


@router.post(
    "/verify/{rqtKey}/resend-callback",
    response_model=ApiResponse[None],
    summary="완료된 job의 저장된 결과를 콜백으로 재전송 (재검증 없음)",
)
async def resend_callback(rqtKey: str, background_tasks: BackgroundTasks) -> ApiResponse[None]:
    job = await terms_job_service.get_job(rqtKey)
    if job is None:
        raise DocumentNotFoundError()
    if job["status"] != "completed":
        raise InvalidRequestError("완료된 job만 재전송할 수 있습니다.")

    background_tasks.add_task(terms_verification_service.resend_stored_result, job)

    return ApiResponse[None](statusCode=200, statusMsg="재전송을 시작했습니다.", result=None)
