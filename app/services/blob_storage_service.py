from functools import lru_cache

from azure.core.exceptions import HttpResponseError, ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import BlobServiceClient, ContainerClient

from app.core.config import settings
from app.exceptions.handlers import BlobNotFoundError, BlobStorageError


@lru_cache
def get_blob_service_client() -> BlobServiceClient:
    return BlobServiceClient.from_connection_string(settings.azure_storage_connection_string)


def get_container_client() -> ContainerClient:
    container_client = get_blob_service_client().get_container_client(
        settings.azure_storage_container_name
    )

    try:
        container_client.create_container()
    except ResourceExistsError:
        pass

    return container_client


def upload_blob(blob_name: str, content: bytes, overwrite: bool = True) -> str:
    blob_client = get_container_client().get_blob_client(blob_name)

    try:
        blob_client.upload_blob(content, overwrite=overwrite)
    except HttpResponseError as exc:
        raise BlobStorageError(str(exc)) from exc

    return blob_client.url


def download_blob(blob_name: str) -> bytes:
    blob_client = get_container_client().get_blob_client(blob_name)

    try:
        return blob_client.download_blob().readall()
    except ResourceNotFoundError as exc:
        raise BlobNotFoundError(blob_name) from exc


def list_blobs(name_starts_with: str | None = None) -> list[str]:
    return [b.name for b in get_container_client().list_blobs(name_starts_with=name_starts_with)]


def delete_blob(blob_name: str) -> None:
    try:
        get_container_client().get_blob_client(blob_name).delete_blob()
    except ResourceNotFoundError as exc:
        raise BlobNotFoundError(blob_name) from exc
