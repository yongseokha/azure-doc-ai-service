from functools import lru_cache

from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.search.documents.aio import SearchClient
from azure.search.documents.indexes.aio import SearchIndexClient
from azure.search.documents.indexes.models import SearchFieldDataType, SearchIndex, SimpleField

from app.core.config import settings
from app.exceptions.handlers import SearchIndexError


@lru_cache
def get_search_client() -> SearchClient:
    return SearchClient(
        endpoint=settings.azure_search_endpoint,
        index_name=settings.azure_search_index_name,
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
        name=settings.azure_search_index_name,
        fields=[
            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
            SimpleField(name="content_hash", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="model_id", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="output_format", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="status", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="first_seen_filename", type=SearchFieldDataType.String),
            SimpleField(name="latest_filename", type=SearchFieldDataType.String),
            SimpleField(name="content_type", type=SearchFieldDataType.String),
            SimpleField(name="size_bytes", type=SearchFieldDataType.Int64),
            SimpleField(name="original_file_path", type=SearchFieldDataType.String),
            SimpleField(name="result_file_path", type=SearchFieldDataType.String),
            SimpleField(name="result_json_path", type=SearchFieldDataType.String),
            SimpleField(name="char_count", type=SearchFieldDataType.Int32),
            SimpleField(name="page_count", type=SearchFieldDataType.Int32),
            SimpleField(name="processing_duration_seconds", type=SearchFieldDataType.Double),
            SimpleField(name="error_message", type=SearchFieldDataType.String),
            SimpleField(name="hit_count", type=SearchFieldDataType.Int32),
            SimpleField(name="created_at", type=SearchFieldDataType.DateTimeOffset, filterable=True),
            SimpleField(name="updated_at", type=SearchFieldDataType.DateTimeOffset, filterable=True),
            SimpleField(name="last_accessed_at", type=SearchFieldDataType.DateTimeOffset),
        ],
    )


async def ensure_index_exists() -> None:
    index_client = get_index_client()
    try:
        await index_client.get_index(settings.azure_search_index_name)
    except ResourceNotFoundError:
        await index_client.create_index(_build_index())


async def get_document(key: str) -> dict | None:
    try:
        return await get_search_client().get_document(key=key)
    except ResourceNotFoundError:
        return None


async def merge_or_upload_document(document: dict) -> None:
    try:
        await get_search_client().merge_or_upload_documents([document])
    except HttpResponseError as exc:
        raise SearchIndexError(str(exc)) from exc
