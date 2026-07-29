from pydantic import BaseModel


class ParsedDocument(BaseModel):
    cache_hit: bool
    document_hash: str
    result_file_path: str
    result_json_path: str
