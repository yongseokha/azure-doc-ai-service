from datetime import datetime, timedelta, timezone
from functools import lru_cache

from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.search.documents.aio import SearchClient
from azure.search.documents.indexes.aio import SearchIndexClient
from azure.search.documents.indexes.models import SearchFieldDataType, SearchIndex, SimpleField

from app.core.config import settings
from app.exceptions.handlers import SearchIndexError
from app.schemas.terms import TermsVerificationRequest, UsageSummary

STALE_PROCESSING_THRESHOLD = timedelta(hours=2)


@lru_cache
def get_search_client() -> SearchClient:
    return SearchClient(
        endpoint=settings.azure_search_endpoint,
        index_name=settings.azure_search_terms_index_name,
        credential=AzureKeyCredential(settings.azure_search_api_key),
    )


@lru_cache
def get_index_client() -> SearchIndexClient:
    return SearchIndexClient(
        endpoint=settings.azure_search_endpoint,
        credential=AzureKeyCredential(settings.azure_search_api_key),
    )


async def close() -> None:
    if get_search_client.cache_info().currsize > 0:
        await get_search_client().close()
        get_search_client.cache_clear()
    if get_index_client.cache_info().currsize > 0:
        await get_index_client().close()
        get_index_client.cache_clear()


def _build_index() -> SearchIndex:
    return SearchIndex(
        name=settings.azure_search_terms_index_name,
        fields=[
            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
            SimpleField(name="user_id", type=SearchFieldDataType.String),
            SimpleField(name="inf_id", type=SearchFieldDataType.String),
            SimpleField(name="status", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="document_count", type=SearchFieldDataType.Int32),
            SimpleField(name="names_count", type=SearchFieldDataType.Int32),
            SimpleField(name="item_count", type=SearchFieldDataType.Int32),
            SimpleField(name="model", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="total_prompt_tokens", type=SearchFieldDataType.Int32),
            SimpleField(name="total_completion_tokens", type=SearchFieldDataType.Int32),
            SimpleField(name="total_cached_tokens", type=SearchFieldDataType.Int32),
            SimpleField(name="total_elapsed_ms", type=SearchFieldDataType.Double),
            SimpleField(name="result_file_path", type=SearchFieldDataType.String),
            SimpleField(name="knwlg_info_id", type=SearchFieldDataType.Int64),
            SimpleField(name="term_vrf_seq", type=SearchFieldDataType.Int32),
            SimpleField(name="callback_status", type=SearchFieldDataType.String),
            SimpleField(name="callback_message", type=SearchFieldDataType.String),
            SimpleField(name="error_message", type=SearchFieldDataType.String),
            SimpleField(name="created_at", type=SearchFieldDataType.DateTimeOffset, filterable=True),
            SimpleField(name="updated_at", type=SearchFieldDataType.DateTimeOffset, filterable=True),
            SimpleField(name="completed_at", type=SearchFieldDataType.DateTimeOffset),
        ],
    )


async def ensure_index_exists() -> None:
    index_client = get_index_client()
    try:
        await index_client.get_index(settings.azure_search_terms_index_name)
    except ResourceNotFoundError:
        await index_client.create_index(_build_index())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_stale(updated_at) -> bool:
    if isinstance(updated_at, str):
        updated_at = datetime.fromisoformat(updated_at)
    return datetime.now(timezone.utc) - updated_at > STALE_PROCESSING_THRESHOLD


async def _merge_or_upload(document: dict) -> None:
    try:
        await get_search_client().merge_or_upload_documents([document])
    except HttpResponseError as exc:
        raise SearchIndexError(str(exc)) from exc


async def get_job(rqt_key: str) -> dict | None:
    try:
        return await get_search_client().get_document(key=rqt_key)
    except ResourceNotFoundError:
        return None


async def claim_job(request: TermsVerificationRequest) -> None:
    """새 job이든, 처리중 상태가 stale해서 재처리하는 job이든 동일하게 처리 상태로 (재)기록한다."""
    now = _now_iso()
    item_count = sum(len(group.items) for group in request.data)
    await _merge_or_upload(
        {
            "id": request.rqtKey,
            "user_id": request.userId,
            "inf_id": request.infId,
            "status": "processing",
            "document_count": len(request.documentHash),
            "names_count": len(request.data),
            "item_count": item_count,
            "knwlg_info_id": request.knwlgInfoId,
            "term_vrf_seq": request.termVrfSeq,
            "callback_status": "pending",
            "error_message": None,
            "created_at": now,
            "updated_at": now,
        }
    )


async def mark_completed(rqt_key: str, result_file_path: str, usage: UsageSummary) -> None:
    await _merge_or_upload(
        {
            "id": rqt_key,
            "status": "completed",
            "result_file_path": result_file_path,
            "model": usage.model,
            "total_prompt_tokens": usage.totalPromptTokens,
            "total_completion_tokens": usage.totalCompletionTokens,
            "total_cached_tokens": usage.totalCachedTokens,
            "total_elapsed_ms": usage.totalElapsedMs,
            "updated_at": _now_iso(),
            "completed_at": _now_iso(),
        }
    )


async def mark_failed(rqt_key: str, error_message: str) -> None:
    await _merge_or_upload(
        {
            "id": rqt_key,
            "status": "failed",
            "error_message": error_message,
            "updated_at": _now_iso(),
            "completed_at": _now_iso(),
        }
    )


async def record_callback_result(rqt_key: str, success: bool, message: str | None = None) -> None:
    await _merge_or_upload(
        {
            "id": rqt_key,
            "callback_status": "success" if success else "failed",
            "callback_message": message,
            "updated_at": _now_iso(),
        }
    )
