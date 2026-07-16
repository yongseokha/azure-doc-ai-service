from typing import Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatCompletionRequest(BaseModel):
    messages: list[ChatMessage]
    max_output_tokens: int = Field(default=800, ge=1, le=4096)


class ChatCompletionResponse(BaseModel):
    content: str
