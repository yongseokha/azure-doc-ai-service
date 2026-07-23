from pydantic import BaseModel


class ParsedDocument(BaseModel):
    char_count: int
    cache_hit: bool = False
    document_hash: str | None = None
    result_file_path: str | None = None
