import uuid
from functools import lru_cache

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.fileshare.aio import ShareServiceClient
from azure.storage.fileshare import FileProperties

from app.core.config import settings
from app.exceptions.handlers import FileStorageError


@lru_cache
def get_share_service_client() -> ShareServiceClient:
    return ShareServiceClient.from_connection_string(settings.azure_file_storage_connection_string)


async def close() -> None:
    if get_share_service_client.cache_info().currsize == 0:
        return
    await get_share_service_client().close()
    get_share_service_client.cache_clear()


async def ensure_share_exists() -> None:
    share_client = get_share_service_client().get_share_client(settings.azure_file_share_name)
    try:
        await share_client.create_share()
    except ResourceExistsError:
        pass


async def _ensure_directories(path: str) -> None:
    """path의 파일명을 제외한 각 디렉터리 계층을 순서대로 생성.
    Azure Files는 중간 디렉터리를 한 번에 재귀 생성하지 않으므로 상위부터 하나씩 만든다."""
    share_client = get_share_service_client().get_share_client(settings.azure_file_share_name)
    parts = path.split("/")[:-1]
    current = ""
    for part in parts:
        current = f"{current}/{part}" if current else part
        try:
            await share_client.get_directory_client(current).create_directory()
        except ResourceExistsError:
            pass


async def upload_file(path: str, content: bytes) -> None:
    """임시 경로에 업로드를 완료한 뒤 원자적으로 rename하여, 쓰는 도중의 파일이 노출되지 않게 한다."""
    await _ensure_directories(path)

    share_client = get_share_service_client().get_share_client(settings.azure_file_share_name)
    temp_path = f"{path}.uploading-{uuid.uuid4().hex}"
    temp_file_client = share_client.get_file_client(temp_path)

    try:
        await temp_file_client.upload_file(content)
        await temp_file_client.rename_file(path, overwrite=True)
    except Exception as exc:
        try:
            await temp_file_client.delete_file()
        except ResourceNotFoundError:
            pass
        raise FileStorageError(str(exc)) from exc


async def download_file(path: str) -> bytes:
    share_client = get_share_service_client().get_share_client(settings.azure_file_share_name)
    file_client = share_client.get_file_client(path)

    try:
        downloader = await file_client.download_file()
        return await downloader.readall()
    except ResourceNotFoundError as exc:
        raise FileStorageError(f"파일을 찾을 수 없습니다: {path}") from exc


async def get_file_properties(path: str) -> FileProperties:
    share_client = get_share_service_client().get_share_client(settings.azure_file_share_name)
    file_client = share_client.get_file_client(path)

    try:
        return await file_client.get_file_properties()
    except ResourceNotFoundError as exc:
        raise FileStorageError(f"파일을 찾을 수 없습니다: {path}") from exc
