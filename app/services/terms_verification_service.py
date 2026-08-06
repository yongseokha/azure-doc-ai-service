import asyncio
import json
from typing import Any

from app.core.config import settings
from app.schemas.terms import (
    DocumentReference,
    TermsDocumentItemsResult,
    TermsItem,
    TermsItemResult,
    TermsNameResult,
    TermsVerificationRequest,
    TermsVerificationResult,
    UsageSummary,
)
from app.services import azure_openai_service, callback_service, file_storage_service, ocr_cache_service, terms_job_service
from app.services.azure_openai_service import StructuredCompletion

RESULT_ROOT = "terms-verification"

SYSTEM_PROMPT = (
    "당신은 약관 문서를 기준으로 상품 항목 데이터를 검증하는 어시스턴트입니다. "
    "반드시 주어진 약관 원문 내용만을 근거로 판단하고, 원문에 없는 내용은 추측하지 마세요."
)

ITEM_VERIFICATION_SCHEMA = {
    "name": "terms_item_verification",
    "schema": {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["MATCHED", "MISMATCH", "EXTRACTED", "NOT_FOUND"]},
            "llmValue": {"type": ["string", "null"]},
            "evidence": {"type": ["string", "null"]},
            "reason": {"type": ["string", "null"]},
        },
        "required": ["status", "llmValue", "evidence", "reason"],
        "additionalProperties": False,
    },
    "strict": True,
}


def _build_user_prompt(document_text: str, name: str, item: TermsItem) -> str:
    # 약관 원문을 항상 맨 앞에 고정 배치 - 같은 문서에 대한 반복 호출에서 Azure OpenAI의
    # prompt caching(동일 prefix 재사용) 효과를 받기 위함.
    value_section = f'현재 값: "{item.value}"' if item.value else "현재 값: (없음, 약관에서 추출 필요)"
    return (
        f"[약관 원문]\n{document_text}\n\n"
        f"[검증 대상]\n"
        f"상품명: {name}\n"
        f"항목명: {item.itemNm}\n"
        f"항목 설명: {item.desc or '(없음)'}\n"
        f"{value_section}\n\n"
        "위 약관 원문을 근거로 이 항목을 판단하세요.\n"
        "- 현재 값이 있고 약관 내용과 일치하면 status=MATCHED, llmValue는 현재 값과 동일하게 채우세요.\n"
        "- 현재 값이 있는데 약관 내용과 다르면 status=MISMATCH, llmValue에 약관 기준 정정값을, reason에 차이를 설명하세요.\n"
        "- 현재 값이 없고 약관에서 값을 찾았으면 status=EXTRACTED, llmValue에 추출한 값을 채우세요.\n"
        "- 약관에 이 항목에 대한 내용 자체가 없으면 status=NOT_FOUND, llmValue는 null로 하세요.\n"
        "evidence에는 판단 근거가 된 약관 원문 문장을 생략·의역 없이 그대로 인용하세요."
    )


async def _verify_one(name: str, item: TermsItem, document_text: str) -> tuple[TermsItemResult, StructuredCompletion]:
    completion = await azure_openai_service.create_structured_completion(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=_build_user_prompt(document_text, name, item),
        json_schema=ITEM_VERIFICATION_SCHEMA,
    )
    parsed = json.loads(completion.content)
    result = TermsItemResult(
        itemNm=item.itemNm,
        value=item.value,
        status=parsed["status"],
        llmValue=parsed.get("llmValue"),
        evidence=parsed.get("evidence"),
        reason=parsed.get("reason"),
    )
    return result, completion


async def _load_documents(refs: list[DocumentReference]) -> dict[str, str]:
    unique_hashes = list({ref.hashKey for ref in refs})
    texts = await asyncio.gather(*(ocr_cache_service.get_result_text(h) for h in unique_hashes))
    return dict(zip(unique_hashes, texts))


def _aggregate_usage(metrics: list[StructuredCompletion]) -> UsageSummary:
    count = len(metrics)
    total_elapsed = sum(m.elapsed_ms for m in metrics)
    return UsageSummary(
        llmCallCount=count,
        totalPromptTokens=sum(m.prompt_tokens for m in metrics),
        totalCompletionTokens=sum(m.completion_tokens for m in metrics),
        totalCachedTokens=sum(m.cached_tokens for m in metrics),
        totalElapsedMs=total_elapsed,
        avgElapsedMs=(total_elapsed / count) if count else 0.0,
    )


def _assemble(
    document_hash: list[DocumentReference],
    calls: list[tuple[str, str, TermsItem]],
    item_results: tuple[TermsItemResult, ...],
) -> list[TermsNameResult]:
    ref_by_hash = {ref.hashKey: ref for ref in document_hash}

    by_name: dict[str, dict[str, list[TermsItemResult]]] = {}
    for (name, hash_key, _item), result in zip(calls, item_results):
        by_name.setdefault(name, {}).setdefault(hash_key, []).append(result)

    return [
        TermsNameResult(
            name=name,
            documents=[
                TermsDocumentItemsResult(hashKey=hash_key, termsName=ref_by_hash[hash_key].termsName, items=items)
                for hash_key, items in docs.items()
            ],
        )
        for name, docs in by_name.items()
    ]


async def _verify_all(request: TermsVerificationRequest) -> tuple[list[TermsNameResult], UsageSummary]:
    # hashKey 기준으로 중복 제거 - 같은 문서가 두 번 오면 LLM 호출도 두 번 실행되는 걸 막는다.
    unique_refs = list({ref.hashKey: ref for ref in request.documentHash}.values())
    text_by_hash = await _load_documents(unique_refs)

    calls = [
        (group.name, ref.hashKey, item)
        for group in request.names
        for ref in unique_refs
        for item in group.items
    ]

    semaphore = asyncio.Semaphore(settings.terms_verification_concurrency)

    async def _bounded_verify(name: str, hash_key: str, item: TermsItem) -> tuple[TermsItemResult, StructuredCompletion]:
        async with semaphore:
            return await _verify_one(name, item, text_by_hash[hash_key])

    tasks = [asyncio.create_task(_bounded_verify(name, hash_key, item)) for name, hash_key, item in calls]

    try:
        outcomes = await asyncio.gather(*tasks)
    except Exception:
        # 하나라도 실패하면 전체 요청을 실패로 처리하므로, 남은 진행 중인 호출은 비용 낭비를 막기 위해 취소한다.
        # cancel()은 취소 "요청"만 걸 뿐이라, 실제로 정리될 때까지 기다려야 태스크가 붕 뜬 채 남지 않는다.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    item_results, metrics = zip(*outcomes) if outcomes else ((), ())
    names_result = _assemble(unique_refs, calls, item_results)
    return names_result, _aggregate_usage(list(metrics))


def _build_callback_body(request: TermsVerificationRequest, status_code: int, status_msg: str, result: TermsVerificationResult | None) -> dict:
    # callbackParams를 먼저 깔고 우리 필드를 그 위에 덮어써야, callbackParams 안에 우연히 같은 키
    # (예: rqtKey, statusCode)가 있어도 실제 결과가 클라이언트 값에 덮어써지지 않는다.
    body = dict(request.callbackParams)
    body.update(
        {
            "userId": request.userId,
            "infId": request.infId,
            "rqtKey": request.rqtKey,
            "statusCode": status_code,
            "statusMsg": status_msg,
            "names": [n.model_dump() for n in result.names] if result else None,
            "usage": result.usage.model_dump() if result else None,
        }
    )
    return body


async def process_and_callback(request: TermsVerificationRequest) -> None:
    try:
        names_result, usage = await _verify_all(request)
    except Exception as exc:
        await terms_job_service.mark_failed(request.rqtKey, str(exc))
        body = _build_callback_body(request, 500, f"약관 검증 처리 중 오류가 발생했습니다: {exc}", None)
        success = await callback_service.send_callback(request.callbackUrl, body)
        await terms_job_service.record_callback_result(request.rqtKey, success)
        return

    result = TermsVerificationResult(names=names_result, usage=usage)
    result_path = f"{RESULT_ROOT}/{request.rqtKey}/result.json"
    await file_storage_service.upload_file(
        result_path, result.model_dump_json().encode("utf-8"), content_type="application/json"
    )
    await terms_job_service.mark_completed(request.rqtKey, result_path, usage)

    body = _build_callback_body(request, 200, "OK", result)
    success = await callback_service.send_callback(request.callbackUrl, body)
    await terms_job_service.record_callback_result(request.rqtKey, success)


async def resend_stored_result(job: dict, callback_url: str, callback_params: dict[str, Any]) -> None:
    """재검증 없이, 이미 완료된 job의 저장된 결과를 그대로 다시 콜백으로 전송한다."""
    content = await file_storage_service.download_file(job["result_file_path"])
    stored = json.loads(content.decode("utf-8"))

    body = dict(callback_params)
    body.update(
        {
            "userId": job["user_id"],
            "infId": job["inf_id"],
            "rqtKey": job["id"],
            "statusCode": 200,
            "statusMsg": "OK",
            "names": stored["names"],
            "usage": stored["usage"],
        }
    )
    success = await callback_service.send_callback(callback_url, body)
    await terms_job_service.record_callback_result(job["id"], success)
