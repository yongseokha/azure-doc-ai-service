from functools import lru_cache

from azure.core.exceptions import HttpResponseError, ResourceExistsError, ResourceNotFoundError
from azure.storage.blob.aio import BlobServiceClient, ContainerClient

from app.core.config import settings
from app.exceptions.handlers import BlobNotFoundError, BlobStorageError


@lru_cache
def get_blob_service_client() -> BlobServiceClient:
    return BlobServiceClient.from_connection_string(settings.azure_storage_connection_string)


def get_container_client() -> ContainerClient:
    return get_blob_service_client().get_container_client(settings.azure_storage_container_name)


async def ensure_container_exists() -> None:
    try:
        await get_container_client().create_container()
    except ResourceExistsError:
        pass


async def close() -> None:
    if get_blob_service_client.cache_info().currsize == 0:
        return
    await get_blob_service_client().close()
    get_blob_service_client.cache_clear()


async def upload_blob(blob_name: str, content: bytes, overwrite: bool = True) -> str:
    blob_client = get_container_client().get_blob_client(blob_name)

    try:
        await blob_client.upload_blob(content, overwrite=overwrite)
    except HttpResponseError as exc:
        raise BlobStorageError(str(exc)) from exc

    return blob_client.url


async def download_blob(blob_name: str) -> bytes:
    blob_client = get_container_client().get_blob_client(blob_name)

    try:
        downloader = await blob_client.download_blob()
        return await downloader.readall()
    except ResourceNotFoundError as exc:
        raise BlobNotFoundError(blob_name) from exc


async def list_blobs(name_starts_with: str | None = None) -> list[str]:
    return [
        b.name
        async for b in get_container_client().list_blobs(name_starts_with=name_starts_with)
    ]


async def delete_blob(blob_name: str) -> None:
    try:
        await get_container_client().get_blob_client(blob_name).delete_blob()
    except ResourceNotFoundError as exc:
        raise BlobNotFoundError(blob_name) from exc
