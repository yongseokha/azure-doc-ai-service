import asyncio
import json
import logging
import textwrap
from datetime import datetime

from app.core.config import settings
from app.schemas.terms import (
    DocumentReference,
    SourceGroup,
    TermsAskDocumentItemsResult,
    TermsAskItemResult,
    TermsAskNameResult,
    TermsAskVerificationRequest,
    TermsAskVerificationResult,
    UsageSummary,
    VerificationTarget,
)
from app.services import (
    ask_report_service,
    azure_openai_service,
    callback_service,
    file_storage_service,
    terms_job_service,
)
from app.services.azure_openai_service import StructuredCompletion
from app.services.llm_throttle import job_lock, llm_rate_limiter
from app.services.terms_verification_service import (
    REPORT_CONTENT_TYPE,
    _aggregate_usage,
    _build_callback_data,
    _build_report_filename,
    _load_documents,
    _run_bounded,
    _sanitize_filename_part,
)

logger = logging.getLogger(__name__)

RESULT_ROOT = "terms-ask-verification"

SYSTEM_PROMPT = textwrap.dedent("""\
    당신은 검증 원문에 기재된 항목값이 약관 원문과 일치하는지 검증하는 심사 어시스턴트입니다.
    반드시 약관 원문만을 근거로 판단하세요.
    검증 원문은 상품명, 항목명, 현재값을 파악하기 위한 참고자료이며, 검증 결과의 근거로 사용할 수 없습니다.
    약관 원문에 없는 내용은 추측하지 마세요.

    [검증 대상 특정]
    1. 사용자가 지정한 상품명과 항목명만 검증합니다.
    2. 상품명 뒤에 혜택, 제휴 프로모션명이 붙어도 약관의 기본 요금제명이 일치하면 동일 상품으로 봅니다.
    3. 약관에 없는 부가 문구의 혜택, 조건은 인정하지 않습니다.
    4. 다른 상품의 내용은 적용하지 않습니다.
    5. "검증 원문"의 value와 약관에서 확인한 llmValue는 별도로 비교합니다.
    6. value가 없거나 빈 값이면 약관에 값이 있어도 MISMATCH입니다.
    7. 지정하지 않은 항목은 무시합니다.
    8. 지정한 항목명이 검증 원문의 itemNm과 완전히 일치하지 않더라도, 검증 원문 item의 value에 해당 항목의 값이 직접 포함되어 있으면 해당 item을 비교 대상으로 사용할 수 있습니다. 이때 [검증 대상]의 항목 설명(desc)을 참고해 지정한 항목이 무엇을 의미하는지 판단합니다.

    [검증 절차]
    각 item마다 다음을 수행합니다.
    1. askValue
    "검증 원문"에서 검증 대상 상품과 항목에 대응하는 실제 값을 찾습니다.
     - "검증 원문"에 해당 항목이 있으면 원문 의미를 훼손하지 않는 범위에서 간략히 기입합니다.
     - "검증 원문"에 해당 항목 자체가 없으면 null입니다.
    2. llmValue
    "약관 원문"에서 검증 대상 상품과 항목에 대응하는 실제 값을 찾습니다.
     - "약관 원문"에 해당 항목이 있으면 원문 의미를 훼손하지 않는 범위에서 간략히 기입합니다.
     - "약관 원문"에 해당 항목 자체가 없으면 null입니다.
     - "검증 원문"의 value만으로 값을 추론하지 않습니다.
    3. evidence
    llmValue의 근거가 되는 문장을 "약관 원문"에서 생략·의역 없이 그대로 인용합니다. 근거가 되는 문장이 여러 곳에 있으면 llmValue를 가장 직접적으로 뒷받침하는 문장 하나만 인용합니다. llmValue가 null이면 null입니다.
     - 근거가 <table>/<tr>/<th>/<td> 같은 HTML 표 마크업 안에 있으면 태그를 그대로 인용하지 말고, 셀 텍스트만 뽑아서 같은 행(row)의 값끼리는 콤마(,)로 잇고 서로 다른 행은 줄바꿈으로 구분합니다. 숫자·단어 등 내용은 하나도 빠짐없이 유지하고, 행 사이의 연관관계(예: 어떤 요금제에 어떤 값이 속하는지)를 섞지 않습니다.
    4. page
    evidence가 위치한 페이지 번호를 "약관 원문"의 <!-- PageNumber="N" --> 마커로 판단합니다. 이 마커는 각 페이지 본문 맨 위에 붙어 있으므로, evidence 바로 위에서 가장 가까운 PageNumber 마커의 값을 사용합니다. evidence가 null이면 null입니다.
    5. article
    evidence가 위치한 약관 조항 번호(예: "제3조", "제3조 2항")가 원문에 표기되어 있으면 기입합니다. evidence가 null이거나 조항을 특정할 수 없으면 null입니다.
    6. reason
    askValue와 llmValue를 비교합니다.
     - 완전히 같으면 null입니다.
     - llmValue가 null이면 약관에 해당 항목이 없음을 간략히 설명합니다.
     - 차이가 있으면 다음을 구분합니다.
      a. askValue에 약관에서 확인되지 않는 내용이 있는 경우
      b. llmValue의 내용이 askValue에서 빠진 경우
    7. matchRate
     - reason이 null이면 null
     - llmValue가 null이면 null
     - 그 외에는 의미상 일치 정도를 0~100 정수로 입력합니다.
    8. status
    아래 순서대로 판정하며, 먼저 충족된 조건을 최종 status로 사용합니다.
     8.1 MISMATCH
       - askValue가 없거나 null 또는 빈 값
       - llmValue가 null
       - askValue에 약관과 다르거나 약관에서 확인되지 않는 내용이 하나라도 있음
       약관에서 llmValue가 확인되어도 askValue가 없으면 반드시 MISMATCH
     8.2 PARTIAL_MATCH
       - askValue의 내용은 모두 맞지만, llmValue의 일부 내용이 "검증 원문"에서 누락됨
     8.3 MATCHED
       - askValue와 llmValue가 의미상 완전히 일치
    status는 반드시 askValue와 llmValue를 비교하여 판단합니다.
    9. nameMatchRate
    "검증 원문"의 name이 "약관 원문"에서 실제로 지칭되는 상품과 얼마나 일치하는지 0~100 정수로 판단합니다.
     - 사실상 동일하면 100
     - 일부 표현 차이는 의미에 따라 감점
     - "약관 원문"에 해당 상품이 없으면 0
     - "검증 원문"의 부가 문구가 약관에서 확인되지 않으면 그 부가 문구는 인정하지 않습니다.

    [중요한 처리 기준]
     - askValue에 값이 있어도 "약관 원문"에 없으면 llmValue는 null입니다.
     - 공통 약관은 해당 상품에 공통 적용된다는 근거가 "약관 원문"에 있을 때만 사용합니다.""")

ITEM_VERIFICATION_SCHEMA = {
    "name": "terms_ask_item_verification",
    "schema": {
        "type": "object",
        "properties": {
            "askValue": {"type": ["string", "null"], "description": "검증 원문에서 찾은 값. 해당 항목 자체가 없으면 null"},
            "llmValue": {"type": ["string", "null"], "description": "약관 원문에서 찾은 값. 해당 항목 자체가 없으면 null"},
            "evidence": {"type": ["string", "null"]},
            "page": {
                "type": ["integer", "null"],
                "description": "evidence가 위치한 페이지 번호. evidence 바로 위(앞)에서 가장 가까운 PageNumber 마커 값 (마커는 각 페이지 본문 맨 위에 위치)",
            },
            "article": {
                "type": ["string", "null"],
                "description": "evidence가 위치한 약관 조항 번호, 예: '제3조', '제3조 2항'. 특정할 수 없으면 null",
            },
            "reason": {"type": ["string", "null"]},
            "matchRate": {
                "type": ["integer", "null"],
                "description": "askValue와 llmValue의 의미적 일치율 (0~100). reason이 null이거나 llmValue가 null이면 null",
            },
            "status": {"type": "string", "enum": ["MATCHED", "PARTIAL_MATCH", "MISMATCH"]},
            "nameMatchRate": {
                "type": "integer",
                "description": "검증 원문의 name과 약관 원문 내 실제 표현의 의미적 일치율 (0~100). 언급 자체가 없으면 0",
            },
        },
        "required": [
            "askValue", "llmValue", "evidence", "page", "article", "reason", "matchRate", "status", "nameMatchRate"
        ],
        "additionalProperties": False,
    },
    "strict": True,
}


def _build_user_content(document_text: str, group: SourceGroup, target: VerificationTarget) -> list[dict]:
    # 약관 원문을 항상 맨 앞 블록에 고정 배치하고 명시적 캐시 breakpoint를 걸어서, 같은
    # 문서에 대한 반복 호출에서 Azure OpenAI의 prompt caching 효과를 안정적으로 받는다.
    # 가변적인 [검증 원문]/[검증 대상] 부분은 별도 블록으로 분리해 캐시 경계 뒤에 둔다.
    source_json = json.dumps(group.model_dump(), ensure_ascii=False, indent=2)
    return [
        {
            "type": "text",
            "text": f"[약관 원문]\n{document_text}\n\n",
            "prompt_cache_breakpoint": {"mode": "explicit"},
        },
        {
            "type": "text",
            "text": (
                f"[검증 원문]\n{source_json}\n\n"
                f"[검증 대상]\n"
                f"상품명: {group.name}\n"
                f"항목명: {target.itemNm}\n"
                f"항목 설명: {target.desc or '(없음)'}"
            ),
        },
    ]


async def _verify_one(
    rqtKey: str, group: SourceGroup, hash_key: str, target: VerificationTarget, document_text: str
) -> tuple[TermsAskItemResult, int, StructuredCompletion]:
    await llm_rate_limiter.acquire(rqtKey)
    completion = await azure_openai_service.create_structured_completion(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=_build_user_content(document_text, group, target),
        json_schema=ITEM_VERIFICATION_SCHEMA,
        prompt_cache_key=hash_key,
    )
    parsed = json.loads(completion.content)
    ask_value = parsed.get("askValue")
    llm_value = parsed.get("llmValue")
    status = parsed["status"]
    # askValue/llmValue 중 하나라도 없으면 MISMATCH라는 규칙은 프롬프트에도 있지만,
    # 모델이 어기더라도 결과가 뒤집히지 않도록 여기서 한 번 더 강제한다.
    if not (ask_value and ask_value.strip()) or llm_value is None:
        status = "MISMATCH"
    result = TermsAskItemResult(
        itemNm=target.itemNm,
        askValue=ask_value,
        llmValue=llm_value,
        evidence=parsed.get("evidence"),
        page=parsed.get("page"),
        article=parsed.get("article"),
        reason=parsed.get("reason"),
        matchRate=parsed.get("matchRate"),
        status=status,
    )
    return result, parsed["nameMatchRate"], completion


def _assemble(
    document_hash: list[DocumentReference],
    calls: list[tuple[SourceGroup, str, VerificationTarget]],
    item_results: tuple[TermsAskItemResult, ...],
    name_match_rates: tuple[int, ...],
) -> list[TermsAskNameResult]:
    ref_by_hash = {ref.ocrResltKey: ref for ref in document_hash}

    by_name: dict[str, dict[str, list[tuple[TermsAskItemResult, int]]]] = {}
    for (group, hash_key, _target), entry in zip(calls, zip(item_results, name_match_rates)):
        by_name.setdefault(group.name, {}).setdefault(hash_key, []).append(entry)

    return [
        TermsAskNameResult(
            name=name,
            documents=[
                TermsAskDocumentItemsResult(
                    ocrResltKey=hash_key,
                    termNm=ref_by_hash[hash_key].termNm,
                    aplyDate=ref_by_hash[hash_key].aplyDate,
                    # 같은 (name, 문서) 조합의 콜들은 전부 같은 질문(대상명 일치율)의 답이라 첫 값만 대표로 쓴다.
                    nameMatchRate=entries[0][1],
                    items=[item_result for item_result, _ in entries],
                )
                for hash_key, entries in docs.items()
            ],
        )
        for name, docs in by_name.items()
    ]


async def _verify_all(request: TermsAskVerificationRequest) -> tuple[list[TermsAskNameResult], UsageSummary]:
    try:
        # ocrResltKey 기준으로 중복 제거 - 같은 문서가 두 번 오면 LLM 호출도 두 번 실행되는 걸 막는다.
        unique_refs = list({ref.ocrResltKey: ref for ref in request.termInfo}.values())
        text_by_hash = await _load_documents(unique_refs)

        semaphore = asyncio.Semaphore(settings.terms_verification_concurrency)

        # 문서를 바깥 루프에 둬서 같은 문서에 대한 호출들이 리스트상 서로 붙어있게 한다
        # (prompt 캐시 재사용 텀을 최대한 짧게 유지).
        calls = [
            (group, ref.ocrResltKey, target)
            for ref in unique_refs
            for group in request.data
            for target in request.vrfItem
        ]

        total_calls = len(calls)
        progress_step = max(1, total_calls // 20)  # 전체 구간에서 약 20번만 업데이트하도록 스로틀
        done_count = 0
        await terms_job_service.update_progress(request.rqtKey, 0, total_calls)

        async def _bounded_verify(
            group: SourceGroup, hash_key: str, target: VerificationTarget
        ) -> tuple[TermsAskItemResult, int, StructuredCompletion]:
            nonlocal done_count
            async with semaphore:
                result = await _verify_one(request.rqtKey, group, hash_key, target, text_by_hash[hash_key])
            done_count += 1
            if done_count % progress_step == 0 or done_count == total_calls:
                # 진행률 갱신 실패로 이미 끝난 검증 자체를 job 실패로 만들면 안 된다 - 로그만 남긴다.
                try:
                    await terms_job_service.update_progress(request.rqtKey, done_count, total_calls)
                except Exception:
                    logger.warning("진행률 업데이트 실패 (검증 자체는 계속 진행)", exc_info=True)
            return result

        tasks = [asyncio.create_task(_bounded_verify(*call)) for call in calls]
        outcomes = await _run_bounded(tasks)

        item_results, name_match_rates, metrics = zip(*outcomes) if outcomes else ((), (), ())
        names_result = _assemble(unique_refs, calls, item_results, name_match_rates)
        return names_result, _aggregate_usage(list(metrics))
    finally:
        # job이 끝나면(성공/실패 무관) 이 rqtKey의 rate limit 이력을 정리해서 메모리가 계속 쌓이지 않게 한다.
        llm_rate_limiter.forget(request.rqtKey)


def _callback_url() -> str:
    return settings.terms_ask_verification_callback_url or settings.terms_verification_callback_url


async def process_and_callback(request: TermsAskVerificationRequest) -> None:
    try:
        async with job_lock:
            names_result, usage = await _verify_all(request)
    except Exception as exc:
        await terms_job_service.mark_failed(request.rqtKey, str(exc))
        data = _build_callback_data(
            request.knwlgInfoId, request.termVrfSeq, None, f"약관 검증 처리 중 오류가 발생했습니다: {exc}"
        )
        callback_result = await callback_service.send_callback_multipart(_callback_url(), data)
        await terms_job_service.record_callback_result(request.rqtKey, callback_result.success, callback_result.message)
        return

    result = TermsAskVerificationResult(knwlgNm=request.knwlgNm, data=names_result)
    # Search 문서 키(request.rqtKey)는 원본 그대로 쓰고, 파일 경로에만 안전한 버전을 쓴다.
    safe_rqt_key = _sanitize_filename_part(request.rqtKey)
    result_path = f"{RESULT_ROOT}/{safe_rqt_key}/result.json"
    await file_storage_service.upload_file(
        result_path, result.model_dump_json().encode("utf-8"), content_type="application/json"
    )

    # 리포트(엑셀)와 콜백 JSON이 같은 검증 일시/통계를 쓰도록 여기서 한 번만 계산해서 넘긴다.
    now = datetime.now()
    verified_at = now.strftime("%Y-%m-%d %H:%M:%S")

    report_bytes = await asyncio.to_thread(ask_report_service.build_report_xlsx, result, verified_at)
    report_filename = _build_report_filename(now.strftime("%Y%m%d"), request.knwlgInfoId, request.knwlgNm)
    report_path = f"{RESULT_ROOT}/{safe_rqt_key}/{report_filename}"
    await file_storage_service.upload_file(report_path, report_bytes, content_type=REPORT_CONTENT_TYPE)

    await terms_job_service.mark_completed(request.rqtKey, result_path, report_path, usage)

    vrf_data_reslt_json = json.dumps(ask_report_service.build_result_payload(result, verified_at), ensure_ascii=False)
    data = _build_callback_data(request.knwlgInfoId, request.termVrfSeq, vrf_data_reslt_json, None)
    file_part = (report_filename, report_bytes, REPORT_CONTENT_TYPE)
    callback_result = await callback_service.send_callback_multipart(_callback_url(), data, file_part)
    await terms_job_service.record_callback_result(request.rqtKey, callback_result.success, callback_result.message)


def build_stored_summary(job: dict, stored: dict) -> dict:
    """저장된 result.json(stored)과 job 메타데이터로 검증 일시/통계가 포함된 요약을 만든다.
    콜백 재전송과 GET 상태조회가 이 함수를 공유한다."""
    result = TermsAskVerificationResult.model_validate(stored)

    completed_at = job.get("completed_at")
    if isinstance(completed_at, datetime):
        verified_at = completed_at.strftime("%Y-%m-%d %H:%M:%S")
    elif isinstance(completed_at, str) and completed_at:
        verified_at = completed_at
    else:
        verified_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return ask_report_service.build_result_payload(result, verified_at)


async def resend_stored_result(job: dict) -> None:
    """재검증 없이, 이미 완료된 job의 저장된 결과(및 리포트)를 그대로 다시 콜백으로 전송한다."""
    content = await file_storage_service.download_file(job["result_file_path"])
    stored = json.loads(content.decode("utf-8"))
    summary = build_stored_summary(job, stored)

    file_part = None
    report_path = job.get("report_file_path")
    if report_path:
        report_bytes = await file_storage_service.download_file(report_path)
        created_date = summary["verifiedAt"][:10].replace("-", "")
        report_filename = _build_report_filename(created_date, job.get("knwlg_info_id"), summary.get("knwlgNm"))
        file_part = (report_filename, report_bytes, REPORT_CONTENT_TYPE)

    vrf_data_reslt_json = json.dumps(summary, ensure_ascii=False)
    data = _build_callback_data(job.get("knwlg_info_id"), job.get("term_vrf_seq"), vrf_data_reslt_json, None)
    callback_result = await callback_service.send_callback_multipart(_callback_url(), data, file_part)
    await terms_job_service.record_callback_result(job["id"], callback_result.success, callback_result.message)
