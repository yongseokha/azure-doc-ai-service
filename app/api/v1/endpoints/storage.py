from io import BytesIO

from fastapi import APIRouter, UploadFile
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.schemas.storage import BlobItem, BlobListResult, BlobUploadResult
from app.services import blob_storage_service

router = APIRouter(prefix="/storage", tags=["storage"])


@router.post("/upload", response_model=BlobUploadResult)
async def upload_file(file: UploadFile) -> BlobUploadResult:
    content = await file.read()
    url = await blob_storage_service.upload_blob(file.filename, content)
    return BlobUploadResult(blob_name=file.filename, url=url, size=len(content))


@router.get("", response_model=BlobListResult)
async def list_files() -> BlobListResult:
    names = await blob_storage_service.list_blobs()
    return BlobListResult(
        container=settings.azure_storage_container_name,
        blobs=[BlobItem(name=name) for name in names],
    )


@router.get("/{blob_name:path}")
async def download_file(blob_name: str) -> StreamingResponse:
    content = await blob_storage_service.download_blob(blob_name)
    return StreamingResponse(BytesIO(content), media_type="application/octet-stream")


@router.delete("/{blob_name:path}")
async def delete_file(blob_name: str) -> dict[str, str]:
    await blob_storage_service.delete_blob(blob_name)
    return {"detail": f"'{blob_name}' 삭제되었습니다."}
