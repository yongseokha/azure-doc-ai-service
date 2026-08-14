import asyncio
import json

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
            "page": {
                "type": ["integer", "null"],
                "description": "evidence가 위치한 페이지 번호 (원문의 PageNumber 마커 기준)",
            },
            "reason": {"type": ["string", "null"]},
        },
        "required": ["status", "llmValue", "evidence", "page", "reason"],
        "additionalProperties": False,
    },
    "strict": True,
}

CLAIM_SPLIT_SCHEMA = {
    "name": "claim_split",
    "schema": {
        "type": "object",
        "properties": {
            "claims": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["claims"],
        "additionalProperties": False,
    },
    "strict": True,
}

CLAIM_SPLIT_SYSTEM_PROMPT = "당신은 약관 조건 텍스트를 독립적으로 검증 가능한 단위로 분해하는 어시스턴트입니다."


def _build_user_prompt(document_text: str, name: str, item: TermsItem, claim: str | None) -> str:
    # 약관 원문을 항상 맨 앞에 고정 배치 - 같은 문서에 대한 반복 호출에서 Azure OpenAI의
    # prompt caching(동일 prefix 재사용) 효과를 받기 위함.
    value_section = f'현재 값: "{claim}"' if claim else "현재 값: (없음, 약관에서 추출 필요)"
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
        "evidence에는 판단 근거가 된 약관 원문 문장을 생략·의역 없이 그대로 인용하세요.\n"
        "약관 원문에는 <!-- PageNumber=\"N\" --> 형태의 페이지 마커가 포함되어 있습니다. "
        "evidence가 위치한 페이지 번호를 page 필드에 기입하세요. 특정할 수 없으면 null로 하세요."
    )


def _build_split_prompt(item: TermsItem) -> str:
    return (
        f"[분해 대상]\n항목명: {item.itemNm}\n값: {item.value}\n\n"
        "위 값을 독립적으로 참/거짓 판단이 가능한 조건 단위로 나누세요.\n"
        "- 서로 다른 주제/조건이면 별개 항목으로 분리하세요.\n"
        "- 특정 조건의 예외·결과·부연설명은 관련된 상위 조건에 포함시켜 하나로 유지하세요.\n"
        "- 이미 하나의 독립적인 조건이면 그대로 1개만 반환하세요."
    )


async def _split_claims(item: TermsItem) -> tuple[list[str | None], list[StructuredCompletion]]:
    """item.value를 독립적으로 검증 가능한 조건 단위로 분해한다. value가 없으면 분해 대상이 아니다."""
    if item.value is None:
        return [None], []
    completion = await azure_openai_service.create_structured_completion(
        system_prompt=CLAIM_SPLIT_SYSTEM_PROMPT,
        user_prompt=_build_split_prompt(item),
        json_schema=CLAIM_SPLIT_SCHEMA,
    )
    parsed = json.loads(completion.content)
    return parsed["claims"], [completion]


async def _verify_one(
    name: str, item: TermsItem, claim: str | None, document_text: str
) -> tuple[TermsItemResult, StructuredCompletion]:
    completion = await azure_openai_service.create_structured_completion(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=_build_user_prompt(document_text, name, item, claim),
        json_schema=ITEM_VERIFICATION_SCHEMA,
    )
    parsed = json.loads(completion.content)
    result = TermsItemResult(
        itemNm=item.itemNm,
        value=item.value,
        subClaim=claim if claim != item.value else None,
        status=parsed["status"],
        llmValue=parsed.get("llmValue"),
        evidence=parsed.get("evidence"),
        page=parsed.get("page"),
        reason=parsed.get("reason"),
    )
    return result, completion


async def _load_documents(refs: list[DocumentReference]) -> dict[str, str]:
    unique_hashes = list({ref.ocrResltKey for ref in refs})
    texts = await asyncio.gather(*(ocr_cache_service.get_result_text(h) for h in unique_hashes))
    return dict(zip(unique_hashes, texts))


def _aggregate_usage(metrics: list[StructuredCompletion]) -> UsageSummary:
    count = len(metrics)
    total_elapsed = sum(m.elapsed_ms for m in metrics)
    return UsageSummary(
        model=metrics[0].model if metrics else "",
        llmCallCount=count,
        totalPromptTokens=sum(m.prompt_tokens for m in metrics),
        totalCompletionTokens=sum(m.completion_tokens for m in metrics),
        totalCachedTokens=sum(m.cached_tokens for m in metrics),
        totalElapsedMs=total_elapsed,
        avgElapsedMs=(total_elapsed / count) if count else 0.0,
    )


def _assemble(
    document_hash: list[DocumentReference],
    calls: list[tuple[str, str, TermsItem, str | None]],
    item_results: tuple[TermsItemResult, ...],
) -> list[TermsNameResult]:
    ref_by_hash = {ref.ocrResltKey: ref for ref in document_hash}

    by_name: dict[str, dict[str, list[TermsItemResult]]] = {}
    for (name, hash_key, _item, _claim), result in zip(calls, item_results):
        by_name.setdefault(name, {}).setdefault(hash_key, []).append(result)

    return [
        TermsNameResult(
            name=name,
            documents=[
                TermsDocumentItemsResult(ocrResltKey=hash_key, termNm=ref_by_hash[hash_key].termNm, items=items)
                for hash_key, items in docs.items()
            ],
        )
        for name, docs in by_name.items()
    ]


async def _run_bounded(tasks: list[asyncio.Task]) -> tuple:
    """하나라도 실패하면 전체를 실패로 처리하고, 남은 진행 중인 태스크는 취소 후 정리까지 기다린다."""
    try:
        return await asyncio.gather(*tasks)
    except Exception:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def _verify_all(request: TermsVerificationRequest) -> tuple[list[TermsNameResult], UsageSummary]:
    # ocrResltKey 기준으로 중복 제거 - 같은 문서가 두 번 오면 LLM 호출도 두 번 실행되는 걸 막는다.
    unique_refs = list({ref.ocrResltKey: ref for ref in request.termInfo}.values())
    text_by_hash = await _load_documents(unique_refs)

    semaphore = asyncio.Semaphore(settings.terms_verification_concurrency)

    # 1) name+item 단위로 분해 (termInfo와 무관하게 1번씩만 - 문서 수만큼 중복 분해하지 않는다)
    item_entries = [(group.name, item) for group in request.data for item in group.items]

    async def _bounded_split(item: TermsItem) -> tuple[list[str | None], list[StructuredCompletion]]:
        async with semaphore:
            return await _split_claims(item)

    split_tasks = [asyncio.create_task(_bounded_split(item)) for _, item in item_entries]
    split_outcomes = await _run_bounded(split_tasks)

    claim_entries: list[tuple[str, TermsItem, str | None]] = []
    split_metrics: list[StructuredCompletion] = []
    for (name, item), (claims, completions) in zip(item_entries, split_outcomes):
        split_metrics.extend(completions)
        for claim in claims:
            claim_entries.append((name, item, claim))

    # 2) 분해된 claim들을 termInfo 풀과 교차
    calls = [
        (name, ref.ocrResltKey, item, claim) for name, item, claim in claim_entries for ref in unique_refs
    ]

    async def _bounded_verify(
        name: str, hash_key: str, item: TermsItem, claim: str | None
    ) -> tuple[TermsItemResult, StructuredCompletion]:
        async with semaphore:
            return await _verify_one(name, item, claim, text_by_hash[hash_key])

    tasks = [asyncio.create_task(_bounded_verify(*call)) for call in calls]
    outcomes = await _run_bounded(tasks)

    item_results, metrics = zip(*outcomes) if outcomes else ((), ())
    names_result = _assemble(unique_refs, calls, item_results)
    return names_result, _aggregate_usage(list(metrics) + split_metrics)


def _build_callback_body(request: TermsVerificationRequest, status_code: int, status_msg: str, result: TermsVerificationResult | None) -> dict:
    return {
        "userId": request.userId,
        "infId": request.infId,
        "rqtKey": request.rqtKey,
        "knwlgInfoId": request.knwlgInfoId,
        "termVrfSeq": request.termVrfSeq,
        "statusCode": status_code,
        "statusMsg": status_msg,
        "data": [n.model_dump() for n in result.data] if result else None,
    }


async def process_and_callback(request: TermsVerificationRequest) -> None:
    try:
        names_result, usage = await _verify_all(request)
    except Exception as exc:
        await terms_job_service.mark_failed(request.rqtKey, str(exc))
        body = _build_callback_body(request, 500, f"약관 검증 처리 중 오류가 발생했습니다: {exc}", None)
        callback_result = await callback_service.send_callback(settings.terms_verification_callback_url, body)
        await terms_job_service.record_callback_result(request.rqtKey, callback_result.success, callback_result.message)
        return

    result = TermsVerificationResult(data=names_result)
    result_path = f"{RESULT_ROOT}/{request.rqtKey}/result.json"
    await file_storage_service.upload_file(
        result_path, result.model_dump_json().encode("utf-8"), content_type="application/json"
    )
    await terms_job_service.mark_completed(request.rqtKey, result_path, usage)

    body = _build_callback_body(request, 200, "OK", result)
    callback_result = await callback_service.send_callback(settings.terms_verification_callback_url, body)
    await terms_job_service.record_callback_result(request.rqtKey, callback_result.success, callback_result.message)


async def resend_stored_result(job: dict) -> None:
    """재검증 없이, 이미 완료된 job의 저장된 결과를 그대로 다시 콜백으로 전송한다."""
    content = await file_storage_service.download_file(job["result_file_path"])
    stored = json.loads(content.decode("utf-8"))

    body = {
        "userId": job["user_id"],
        "infId": job["inf_id"],
        "rqtKey": job["id"],
        "knwlgInfoId": job.get("knwlg_info_id"),
        "termVrfSeq": job.get("term_vrf_seq"),
        "statusCode": 200,
        "statusMsg": "OK",
        "data": stored["data"],
    }
    callback_result = await callback_service.send_callback(settings.terms_verification_callback_url, body)
    await terms_job_service.record_callback_result(job["id"], callback_result.success, callback_result.message)
