from pydantic import BaseModel


class ParsedDocument(BaseModel):
    filename: str
    content: str
    char_count: int
    cache_hit: bool = False
    document_hash: str | None = None
