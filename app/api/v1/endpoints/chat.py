from fastapi import APIRouter

from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse
from app.services import azure_openai_service

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/completion", response_model=ChatCompletionResponse)
async def chat_completion(request: ChatCompletionRequest) -> ChatCompletionResponse:
    content = await azure_openai_service.chat_completion(
        messages=request.messages,
        max_output_tokens=request.max_output_tokens,
    )
    return ChatCompletionResponse(content=content)
