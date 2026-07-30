from pydantic import BaseModel, Field


class ParsedDocument(BaseModel):
    cache_hit: bool = Field(examples=[False])
    document_hash: str = Field(examples=["a3f5c9d8e1b2..."])
    result_file_path: str = Field(examples=["cache/a3/a3f5c9d8e1b2.../result.md"])
    result_json_path: str = Field(examples=["cache/a3/a3f5c9d8e1b2.../result.json"])
