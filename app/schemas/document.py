from pydantic import BaseModel


class ParsedDocument(BaseModel):
    char_count: int
    page_count: int
    cache_hit: bool
    document_hash: str
    result_file_path: str
