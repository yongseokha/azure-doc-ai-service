from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.base import ApiRequest


class ParseDiRequest(ApiRequest):
    termId: int = Field(default=0, description="약관 ID", examples=[12345])
    termHstSeq: int = Field(default=0, description="약관 이력 순번", examples=[1])
    fileDivCd: str = Field(default="", description="파일 구분 코드", examples=["01"])
    filePath: str = Field(description="Azure File Storage 내 문서 경로", examples=["uploads/2026-07/계약서.pdf"])


class DocumentState(BaseModel):
    status: Literal["processing", "completed", "failed"] = Field(
        description="처리 상태", examples=["completed"]
    )
    document_hash: str = Field(examples=["a3f5c9d8e1b2..."])
    result_file_path: str | None = Field(default=None, examples=["cache/a3/a3f5c9d8e1b2.../result.md"])
    result_json_path: str | None = Field(default=None, examples=["cache/a3/a3f5c9d8e1b2.../result.json"])
    error_message: str | None = Field(default=None, description="처리 실패 시 오류 메시지", examples=[None])


class ParsedDocument(ApiRequest, DocumentState):
    cache_hit: bool = Field(examples=[False])
