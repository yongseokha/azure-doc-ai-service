from pydantic import BaseModel


class BlobUploadResult(BaseModel):
    blob_name: str
    url: str
    size: int


class BlobItem(BaseModel):
    name: str


class BlobListResult(BaseModel):
    container: str
    blobs: list[BlobItem]
