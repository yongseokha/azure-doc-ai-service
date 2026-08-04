from typing import Literal

from pydantic import Field

from app.schemas.base import ApiRequest


class ParsedDocument(ApiRequest):
    status: Literal["processing", "completed", "failed"] = Field(
        default="completed", description="처리 상태", examples=["completed"]
    )
    cache_hit: bool = Field(examples=[False])
    document_hash: str = Field(examples=["a3f5c9d8e1b2..."])
    result_file_path: str | None = Field(default=None, examples=["cache/a3/a3f5c9d8e1b2.../result.md"])
    result_json_path: str | None = Field(default=None, examples=["cache/a3/a3f5c9d8e1b2.../result.json"])
    error_message: str | None = Field(default=None, description="처리 실패 시 오류 메시지", examples=[None])
