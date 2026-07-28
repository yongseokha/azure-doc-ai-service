import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.exceptions.handlers import StillProcessingError
from app.schemas.document import ParsedDocument
from app.services import document_intelligence_service, file_storage_service, search_index_service
from app.services.document_intelligence_service import DEFAULT_MODEL_ID

OUTPUT_FORMAT = "markdown"
STALE_PROCESSING_THRESHOLD = timedelta(hours=1)
POLL_INTERVAL_SECONDS = 0.5
POLL_TIMEOUT_SECONDS = 30


def _build_cache_key(content_hash: str) -> str:
    return f"{content_hash}_{DEFAULT_MODEL_ID}_{OUTPUT_FORMAT}"


def _build_original_path(content_hash: str, filename: str) -> str:
    extension = Path(filename).suffix
    return f"{content_hash[:2]}/{content_hash}/original{extension}"


def _build_result_path(content_hash: str) -> str:
    return f"{content_hash[:2]}/{content_hash}/result.md"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_stale(updated_at) -> bool:
    if isinstance(updated_at, str):
        updated_at = datetime.fromisoformat(updated_at)
    return datetime.now(timezone.utc) - updated_at > STALE_PROCESSING_THRESHOLD


async def get_or_process(
    content: bytes,
    filename: str,
    content_type: str | None,
    existing_file_path: str | None = None,
) -> ParsedDocument:
    content_hash = hashlib.sha256(content).hexdigest()
    cache_key = _build_cache_key(content_hash)

    doc = await search_index_service.get_document(cache_key)

    if doc is not None and doc["status"] == "completed":
        return await _serve_cached(doc, filename)

    if doc is not None and doc["status"] == "processing" and not _is_stale(doc["updated_at"]):
        return await _poll(cache_key, content_hash, filename, content, content_type, existing_file_path)

    if doc is None:
        await _claim_new(cache_key, content_hash, filename, content_type, len(content))
    else:
        await _reclaim(cache_key)

    return await _process(cache_key, content_hash, filename, content, existing_file_path)


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


async def _process(
    cache_key: str,
    content_hash: str,
    filename: str,
    content: bytes,
    existing_file_path: str | None,
) -> ParsedDocument:
    original_path = existing_file_path or _build_original_path(content_hash, filename)
    result_path = _build_result_path(content_hash)

    try:
        if existing_file_path is None:
            await file_storage_service.upload_file(original_path, content)

        result = await document_intelligence_service.analyze_document(content, model_id=DEFAULT_MODEL_ID)
        text = result.content or ""
        page_count = len(result.pages or [])
        await file_storage_service.upload_file(result_path, text.encode("utf-8"))

        await search_index_service.merge_or_upload_document(
            {
                "id": cache_key,
                "status": "completed",
                "original_file_path": original_path,
                "result_file_path": result_path,
                "char_count": len(text),
                "page_count": page_count,
                "updated_at": _now_iso(),
            }
        )
    except Exception as exc:
        await search_index_service.merge_or_upload_document(
            {"id": cache_key, "status": "failed", "error_message": str(exc), "updated_at": _now_iso()}
        )
        raise

    return ParsedDocument(
        cache_hit=False,
        document_hash=content_hash,
        result_file_path=result_path,
    )


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
        cache_hit=True,
        document_hash=doc["content_hash"],
        result_file_path=doc["result_file_path"],
    )


async def _poll(
    cache_key: str,
    content_hash: str,
    filename: str,
    content: bytes,
    content_type: str | None,
    existing_file_path: str | None,
) -> ParsedDocument:
    elapsed = 0.0
    while elapsed < POLL_TIMEOUT_SECONDS:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        elapsed += POLL_INTERVAL_SECONDS

        doc = await search_index_service.get_document(cache_key)

        if doc is None:
            await _claim_new(cache_key, content_hash, filename, content_type, len(content))
            return await _process(cache_key, content_hash, filename, content, existing_file_path)

        if doc["status"] == "completed":
            return await _serve_cached(doc, filename)

        if doc["status"] == "failed" or _is_stale(doc["updated_at"]):
            await _reclaim(cache_key)
            return await _process(cache_key, content_hash, filename, content, existing_file_path)

    raise StillProcessingError()
