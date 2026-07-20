from functools import lru_cache

from openai import AsyncAzureOpenAI, OpenAIError

from app.core.config import settings
from app.exceptions.handlers import AzureOpenAIError
from app.schemas.chat import ChatMessage


@lru_cache
def get_azure_openai_client() -> AsyncAzureOpenAI:
    return AsyncAzureOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
    )


async def close() -> None:
    if get_azure_openai_client.cache_info().currsize == 0:
        return
    await get_azure_openai_client().close()
    get_azure_openai_client.cache_clear()


async def chat_completion(messages: list[ChatMessage], max_output_tokens: int = 800) -> str:
    client = get_azure_openai_client()

    try:
        response = await client.chat.completions.create(
            model=settings.azure_openai_deployment_name,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            max_completion_tokens=max_output_tokens,
        )
    except OpenAIError as exc:
        raise AzureOpenAIError(str(exc)) from exc

    return response.choices[0].message.content or ""


async def summarize_text(text: str, instruction: str, max_output_tokens: int = 800) -> str:
    messages = [
        ChatMessage(role="system", content="당신은 문서를 정확하고 간결하게 요약/분석하는 어시스턴트입니다."),
        ChatMessage(role="user", content=f"{instruction}\n\n---\n{text}"),
    ]
    return await chat_completion(messages, max_output_tokens=max_output_tokens)
