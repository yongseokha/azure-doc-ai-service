import asyncio
import base64
import json
import textwrap
from datetime import datetime

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
from app.services import (
    azure_openai_service,
    callback_service,
    file_storage_service,
    ocr_cache_service,
    report_service,
    terms_job_service,
)
from app.services.azure_openai_service import StructuredCompletion

RESULT_ROOT = "terms-verification"

# job이 여러 개 동시에 들어와도 Azure OpenAI 호출은 한 번에 한 job씩만 나가도록 직렬화한다.
# (job마다 세마포어를 따로 두면 job 수만큼 동시 호출이 배로 늘어나 rate limit에 취약해진다.)
_job_lock = asyncio.Lock()

SYSTEM_PROMPT = textwrap.dedent("""\
    당신은 약관 문서를 기준으로 상품 항목 데이터를 검증하는 어시스턴트입니다. 반드시 주어진 약관 원문 내용만을 근거로 판단하고, 원문에 없는 내용은 추측하지 마세요.
    itemNm이나 값이 약관 원문에 완전히 동일한 표현으로 나오지 않아도 괜찮습니다. 동의어·유사 표현·어순 차이 등으로 표현만 다를 뿐 의미가 같다면 관련 내용/일치하는 것으로 인정하세요. '일치'는 문자 그대로 같은 표현인지가 아니라 의미가 같은지를 기준으로 판단하세요.

    아래 순서대로 하나씩 판단해서 필드를 채우세요. 뒤 단계는 앞 단계에서 채운 내용을 근거로 판단하세요.

    1. llmValue: 약관 원문 전체(일반 원칙과 예외/단서 조항 모두 포함)를 검토해서 이 항목의 실제 값을 판단해 채우세요.
       - 항목 설명(desc)이 있으면, 항목명(itemNm)이 정확히 무엇을 의미하는지 파악하는 데 참고하세요.
       - 현재 값이 없으면, 상품명+항목명을 기준으로 약관에서 값을 찾아 채우세요.
       - 약관에 이 항목에 대한 내용 자체가 없으면 null로 하세요.
    2. evidence: 위에서 판단한 llmValue의 근거가 되는 문장을 약관 원문에서 생략·의역 없이 그대로 인용하세요. 근거가 되는 문장이 여러 곳에 있으면, llmValue를 가장 직접적으로 뒷받침하는 문장 하나만 인용하세요. llmValue가 null이면 evidence도 null로 하세요.
    3. page: evidence가 위치한 페이지 번호를 원문의 <!-- PageNumber="N" --> 마커를 참고해 기입하세요. evidence가 null이거나 페이지를 특정할 수 없으면 null로 하세요.
    4. article: evidence가 위치한 약관 조항 번호(예: "제3조", "제3조 2항")가 원문에 표기되어 있으면 기입하세요. evidence가 null이거나 조항을 특정할 수 없으면 null로 하세요.
    5. reason: 현재 값과 llmValue가 다른 경우 그 차이를 설명하세요. 현재 값과 llmValue가 완전히 같으면 null로 하세요.
       - 차이가 있다면 그 차이가 다음 중 무엇인지 구분해서 명시하세요: ① 현재 값에 llmValue와 다른(틀린) 내용이 있는 경우 → 어떤 부분이 근거상 확인되지 않는/틀린 내용인지 명시하세요. ② llmValue에 있는 내용이 현재 값에서 빠진(누락된) 경우 → 어떤 내용이 빠졌는지 명시하세요. 두 가지가 함께 있다면 둘 다 명시하세요. '누락되었습니다' 같은 표현은 실제로 빠진 내용에 대해서만 쓰고, 현재 값 자체가 틀린 경우에는 '틀린 내용입니다/근거에서 확인되지 않습니다' 등으로 명확히 구분해서 표현하세요.
    6. status: 1~5에서 채운 내용을 종합해 다음 기준으로 최종 판정하세요. 세 상태는 서로 겹치지 않아야 합니다.
       - MATCHED: 현재 값이 llmValue와 완전히 일치 (틀린 내용도 없고 빠진 내용도 없음)
       - PARTIAL_MATCH: 현재 값에 llmValue와 다른(틀린) 내용은 없지만, llmValue에 있는 내용 중 일부가 현재 값에 빠져 있음 (현재 값에 포함된 내용 자체는 모두 맞음)
       - MISMATCH: 다음 중 하나 — (a) 현재 값에 llmValue와 다른(틀린) 내용이 하나라도 있음, (b) 현재 값 자체가 없음, (c) llmValue가 null(약관에 이 항목에 대한 내용 자체가 없음)""")

ITEM_VERIFICATION_SCHEMA = {
    "name": "terms_item_verification",
    "schema": {
        "type": "object",
        "properties": {
            "llmValue": {"type": ["string", "null"]},
            "evidence": {"type": ["string", "null"]},
            "page": {
                "type": ["integer", "null"],
                "description": "evidence가 위치한 페이지 번호 (원문의 PageNumber 마커 기준)",
            },
            "article": {
                "type": ["string", "null"],
                "description": "evidence가 위치한 약관 조항 번호, 예: '제3조', '제3조 2항'. 특정할 수 없으면 null",
            },
            "reason": {"type": ["string", "null"]},
            "status": {"type": "string", "enum": ["MATCHED", "PARTIAL_MATCH", "MISMATCH"]},
        },
        "required": ["llmValue", "evidence", "page", "article", "reason", "status"],
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
        f"{value_section}"
    )


def _build_split_prompt(item: TermsItem) -> str:
    return (
        f"[분해 대상]\n항목명: {item.itemNm}\n값: {item.value}\n\n"
        "위 값을 독립적으로 참/거짓 판단이 가능한 조건 단위로 나누세요.\n"
        "- 서로 다른 주제/조건이면 별개 항목으로 분리하세요.\n"
        "- 특정 조건의 예외·결과·부연설명은 관련된 상위 조건에 포함시켜 하나로 유지하세요.\n"
        "- 이미 하나의 독립적인 조건이면 그대로 1개만 반환하세요.\n"
        "- 원본 값에 있는 모든 내용을 빠짐없이 어느 조건 하나에는 포함시키세요. 어떤 내용도 누락하거나 생략하지 마세요."
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
        article=parsed.get("article"),
        reason=parsed.get("reason"),
    )
    return result, completion


async def _load_documents(refs: list[DocumentReference]) -> dict[str, str]:
    unique_hashes = list({ref.ocrResltKey for ref in refs})
    texts = await asyncio.gather(*(ocr_cache_service.get_result_text(h) for h in unique_hashes))
    return dict(zip(unique_hashes, texts))


def _aggregate_usage(metrics: list[StructuredCompletion]) -> UsageSummary:
    count = len(metrics)
    total_elapsed_seconds = sum(m.elapsed_ms for m in metrics) / 1000
    return UsageSummary(
        model=metrics[0].model if metrics else "",
        llmCallCount=count,
        totalPromptTokens=sum(m.prompt_tokens for m in metrics),
        totalCompletionTokens=sum(m.completion_tokens for m in metrics),
        totalCachedTokens=sum(m.cached_tokens for m in metrics),
        totalElapsedSeconds=round(total_elapsed_seconds, 2),
        avgElapsedSeconds=round(total_elapsed_seconds / count, 2) if count else 0.0,
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

    total_calls = len(calls)
    progress_step = max(1, total_calls // 20)  # 전체 구간에서 약 20번만 업데이트하도록 스로틀
    done_count = 0
    await terms_job_service.update_progress(request.rqtKey, 0, total_calls)

    async def _bounded_verify(
        name: str, hash_key: str, item: TermsItem, claim: str | None
    ) -> tuple[TermsItemResult, StructuredCompletion]:
        nonlocal done_count
        async with semaphore:
            result = await _verify_one(name, item, claim, text_by_hash[hash_key])
        done_count += 1
        if done_count % progress_step == 0 or done_count == total_calls:
            await terms_job_service.update_progress(request.rqtKey, done_count, total_calls)
        return result

    tasks = [asyncio.create_task(_bounded_verify(*call)) for call in calls]
    outcomes = await _run_bounded(tasks)

    item_results, metrics = zip(*outcomes) if outcomes else ((), ())
    names_result = _assemble(unique_refs, calls, item_results)
    return names_result, _aggregate_usage(list(metrics) + split_metrics)


def _build_callback_body(
    knwlg_info_id: int,
    term_vrf_seq: int,
    vrf_data_reslt_json: str | None,
    err_sbst: str | None,
    file_b64: str | None = None,
) -> dict:
    return {
        "data": {
            "knwlgInfoId": knwlg_info_id,
            "termVrfSeq": term_vrf_seq,
            "vrfDataResltJson": vrf_data_reslt_json,
            "errSbst": err_sbst,
        },
        "file": file_b64,
    }


async def process_and_callback(request: TermsVerificationRequest) -> None:
    try:
        async with _job_lock:
            names_result, usage = await _verify_all(request)
    except Exception as exc:
        await terms_job_service.mark_failed(request.rqtKey, str(exc))
        body = _build_callback_body(
            request.knwlgInfoId, request.termVrfSeq, None, f"약관 검증 처리 중 오류가 발생했습니다: {exc}"
        )
        callback_result = await callback_service.send_callback(settings.terms_verification_callback_url, body)
        await terms_job_service.record_callback_result(request.rqtKey, callback_result.success, callback_result.message)
        return

    result = TermsVerificationResult(data=names_result)
    result_path = f"{RESULT_ROOT}/{request.rqtKey}/result.json"
    await file_storage_service.upload_file(
        result_path, result.model_dump_json().encode("utf-8"), content_type="application/json"
    )

    # 리포트(엑셀)와 콜백 JSON이 같은 검증 일시/통계를 쓰도록 여기서 한 번만 계산해서 넘긴다.
    verified_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    overview_stats = report_service.compute_overview_stats(result)

    report_bytes = await asyncio.to_thread(report_service.build_report_xlsx, result, verified_at)
    report_path = f"{RESULT_ROOT}/{request.rqtKey}/report.xlsx"
    await file_storage_service.upload_file(
        report_path,
        report_bytes,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    await terms_job_service.mark_completed(request.rqtKey, result_path, report_path, usage)

    vrf_data_reslt_json = json.dumps(
        {
            "verifiedAt": verified_at,
            **overview_stats,
            "items": [n.model_dump() for n in result.data],
        },
        ensure_ascii=False,
    )
    file_b64 = base64.b64encode(report_bytes).decode("ascii")
    body = _build_callback_body(request.knwlgInfoId, request.termVrfSeq, vrf_data_reslt_json, None, file_b64)
    callback_result = await callback_service.send_callback(settings.terms_verification_callback_url, body)
    await terms_job_service.record_callback_result(request.rqtKey, callback_result.success, callback_result.message)


async def resend_stored_result(job: dict) -> None:
    """재검증 없이, 이미 완료된 job의 저장된 결과(및 리포트)를 그대로 다시 콜백으로 전송한다."""
    content = await file_storage_service.download_file(job["result_file_path"])
    stored = json.loads(content.decode("utf-8"))
    result = TermsVerificationResult.model_validate(stored)

    completed_at = job.get("completed_at")
    if isinstance(completed_at, datetime):
        verified_at = completed_at.strftime("%Y-%m-%d %H:%M:%S")
    elif isinstance(completed_at, str) and completed_at:
        verified_at = completed_at
    else:
        verified_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    overview_stats = report_service.compute_overview_stats(result)

    file_b64 = None
    report_path = job.get("report_file_path")
    if report_path:
        report_bytes = await file_storage_service.download_file(report_path)
        file_b64 = base64.b64encode(report_bytes).decode("ascii")

    vrf_data_reslt_json = json.dumps(
        {
            "verifiedAt": verified_at,
            **overview_stats,
            "items": stored["data"],
        },
        ensure_ascii=False,
    )
    body = _build_callback_body(
        job.get("knwlg_info_id"),
        job.get("term_vrf_seq"),
        vrf_data_reslt_json,
        None,
        file_b64,
    )
    callback_result = await callback_service.send_callback(settings.terms_verification_callback_url, body)
    await terms_job_service.record_callback_result(job["id"], callback_result.success, callback_result.message)
