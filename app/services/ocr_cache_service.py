import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import settings
from app.exceptions.handlers import DocumentIntelligenceError, DocumentNotFoundError, DocumentNotReadyError
from app.schemas.document import DocumentState, ParsedDocument
from app.services import callback_service, document_intelligence_service, file_storage_service, search_index_service
from app.services.document_intelligence_service import DEFAULT_MODEL_ID

logger = logging.getLogger(__name__)

OUTPUT_FORMAT = "markdown"
STALE_PROCESSING_THRESHOLD = timedelta(minutes=20)


def _build_cache_key(content_hash: str) -> str:
    return f"{content_hash}_{DEFAULT_MODEL_ID}_{OUTPUT_FORMAT}"


CACHE_ROOT = "cache"


def _build_original_path(content_hash: str, filename: str) -> str:
    extension = Path(filename).suffix
    return f"{CACHE_ROOT}/{content_hash[:2]}/{content_hash}/original{extension}"


def _build_result_path(content_hash: str) -> str:
    return f"{CACHE_ROOT}/{content_hash[:2]}/{content_hash}/result.md"


def _build_result_json_path(content_hash: str) -> str:
    return f"{CACHE_ROOT}/{content_hash[:2]}/{content_hash}/result.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_stale(updated_at) -> bool:
    if isinstance(updated_at, str):
        updated_at = datetime.fromisoformat(updated_at)
    return datetime.now(timezone.utc) - updated_at > STALE_PROCESSING_THRESHOLD


async def claim(
    content: bytes, filename: str, content_type: str | None
) -> tuple[str, str, ParsedDocument | None]:
    """캐시를 확인하고, 처리가 필요하면 처리 권한을 선점한다.

    반환값은 (cache_key, content_hash, result)이며, result가 있으면
    (완료된 캐시 또는 이미 처리 중) 그대로 응답에 쓰면 되고, None이면
    호출자가 process_and_store()를 (백그라운드로) 실행해야 한다.
    """
    content_hash = hashlib.sha256(content).hexdigest()
    cache_key = _build_cache_key(content_hash)

    doc = await search_index_service.get_document(cache_key)

    if doc is not None and doc["status"] == "completed":
        return cache_key, content_hash, await _serve_cached(doc, filename)

    if doc is not None and doc["status"] == "processing" and not _is_stale(doc["updated_at"]):
        processing = ParsedDocument(status="processing", cache_hit=False, document_hash=content_hash)
        return cache_key, content_hash, processing

    if doc is None:
        await _claim_new(cache_key, content_hash, filename, content_type, len(content))
    else:
        await _reclaim(cache_key)

    return cache_key, content_hash, None


async def get_status(document_hash: str) -> DocumentState | None:
    cache_key = _build_cache_key(document_hash)
    doc = await search_index_service.get_document(cache_key)
    if doc is None:
        return None

    return DocumentState(
        status=doc["status"],
        document_hash=document_hash,
        result_file_path=doc.get("result_file_path"),
        result_json_path=doc.get("result_json_path"),
        error_message=doc.get("error_message"),
    )


async def get_result_text(document_hash: str) -> str:
    """document_hash로 캐시된 OCR 결과(markdown 본문)를 가져온다.

    약관검증 서비스처럼 이미 parse-di로 처리된 문서를 재사용하는 경우에 쓴다.
    """
    state = await get_status(document_hash)
    if state is None:
        raise DocumentNotFoundError()
    if state.status == "processing":
        raise DocumentNotReadyError(document_hash)
    if state.status == "failed":
        raise DocumentIntelligenceError(state.error_message or "OCR 처리에 실패했습니다")

    content = await file_storage_service.download_file(state.result_file_path)
    return content.decode("utf-8")


async def _claim_new(
    cache_key: str, content_hash: str, filename: str, content_type: str | None, size_bytes: int
) -> None:
    now = _now_iso()
    await search_index_service.merge_or_upload_document(
        {
            "id": cache_key,
            "content_hash": content_hash,
            "model_id": DEFAULT_MODEL_ID,
            "output_format": OUTPUT_FORMAT,
            "status": "processing",
            "first_seen_filename": filename,
            "latest_filename": filename,
            "content_type": content_type,
            "size_bytes": size_bytes,
            "hit_count": 0,
            "created_at": now,
            "updated_at": now,
        }
    )


async def _reclaim(cache_key: str) -> None:
    await search_index_service.merge_or_upload_document(
        {"id": cache_key, "status": "processing", "updated_at": _now_iso()}
    )


async def _send_di_callback(
    term_id: str, term_hst_seq: str, file_div_cd: str, ocr_reslt_key: str | None, ocr_err_sbst: str | None
) -> None:
    body = {
        "termId": term_id,
        "termHstSeq": term_hst_seq,
        "fileDivCd": file_div_cd,
        "ocrResltKey": ocr_reslt_key,
        "ocrErrSbst": ocr_err_sbst,
    }
    try:
        await callback_service.send_callback(settings.document_intelligence_callback_url, body)
    except Exception:
        # 콜백 전송 문제가 OCR 성공/실패 판단에 영향을 주면 안 되므로, 여기서 무슨 예외가 나든 삼킨다.
        logger.exception("DI 콜백 전송 중 예상하지 못한 오류가 발생했습니다.")


async def process_and_store(
    cache_key: str,
    content_hash: str,
    filename: str,
    content: bytes,
    content_type: str | None,
    existing_file_path: str | None,
    term_id: str,
    term_hst_seq: str,
    file_div_cd: str,
) -> None:
    """실제 OCR 처리를 수행하고 결과를 저장한다. 백그라운드 태스크로 실행되므로
    호출자에게 반환할 응답이 없고, 성공/실패 여부는 상태 저장소에 기록되며 콜백으로도 전송된다."""
    original_path = existing_file_path or _build_original_path(content_hash, filename)
    result_path = _build_result_path(content_hash)
    result_json_path = _build_result_json_path(content_hash)

    try:
        if existing_file_path is None:
            await file_storage_service.upload_file(original_path, content, content_type=content_type)

        started_at = time.monotonic()
        result = await document_intelligence_service.analyze_document(content, model_id=DEFAULT_MODEL_ID)
        processing_duration_seconds = time.monotonic() - started_at
        text = result.content or ""
        page_count = len(result.pages or [])
        result_json = await asyncio.to_thread(lambda: json.dumps(result.as_dict(), ensure_ascii=False))

        await file_storage_service.upload_file(result_path, text.encode("utf-8"))
        await file_storage_service.upload_file(result_json_path, result_json.encode("utf-8"))

        await search_index_service.merge_or_upload_document(
            {
                "id": cache_key,
                "status": "completed",
                "original_file_path": original_path,
                "result_file_path": result_path,
                "result_json_path": result_json_path,
                "char_count": len(text),
                "page_count": page_count,
                "processing_duration_seconds": processing_duration_seconds,
                "error_message": None,
                "updated_at": _now_iso(),
            }
        )
        await _send_di_callback(term_id, term_hst_seq, file_div_cd, ocr_reslt_key=content_hash, ocr_err_sbst=None)
    except Exception as exc:
        await search_index_service.merge_or_upload_document(
            {"id": cache_key, "status": "failed", "error_message": str(exc), "updated_at": _now_iso()}
        )
        await _send_di_callback(term_id, term_hst_seq, file_div_cd, ocr_reslt_key=None, ocr_err_sbst=str(exc))


async def _serve_cached(doc: dict, filename: str) -> ParsedDocument:
    await search_index_service.merge_or_upload_document(
        {
            "id": doc["id"],
            "hit_count": doc["hit_count"] + 1,
            "last_accessed_at": _now_iso(),
            "latest_filename": filename,
        }
    )

    return ParsedDocument(
        status="completed",
        cache_hit=True,
        document_hash=doc["content_hash"],
        result_file_path=doc["result_file_path"],
        result_json_path=doc["result_json_path"],
    )
