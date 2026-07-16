from pydantic import BaseModel, Field


class ParsedDocument(BaseModel):
    filename: str
    content: str
    char_count: int


class SummarizeOptions(BaseModel):
    instruction: str = Field(
        default="다음 문서를 핵심 위주로 요약해줘.",
        description="GPT-5에게 전달할 요약/분석 지시사항",
    )
    max_output_tokens: int = Field(default=800, ge=1, le=4096)


class SummarizeResult(BaseModel):
    filename: str
    summary: str
    char_count: int
