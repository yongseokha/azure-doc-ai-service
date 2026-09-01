import time
from dataclasses import dataclass
from functools import lru_cache

from openai import APIError, AsyncAzureOpenAI

from app.core.config import settings
from app.exceptions.handlers import AzureOpenAIError


@lru_cache
def get_client() -> AsyncAzureOpenAI:
    return AsyncAzureOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
        timeout=settings.azure_openai_timeout_seconds,
        max_retries=3,
    )


async def close() -> None:
    if get_client.cache_info().currsize == 0:
        return
    await get_client().close()
    get_client.cache_clear()


@dataclass
class StructuredCompletion:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    cache_write_tokens: int
    elapsed_ms: float


async def create_structured_completion(
    system_prompt: str,
    user_prompt: str | list[dict],
    json_schema: dict,
    prompt_cache_key: str | None = None,
) -> StructuredCompletion:
    """response_format용 json_schema는 {"name", "schema", "strict"} 형태 그대로 전달.

    prompt_cache_key를 주면 같은 키를 공유하는 요청들끼리 캐시 매칭/라우팅이 개선된다
    (GPT-5.6+ 전용). 설치된 openai SDK가 아직 이 파라미터를 모르는 버전이라 extra_body로
    우회해서 실어 보낸다. user_prompt는 content-block 배열(list[dict])로 넘기면 그 안에
    prompt_cache_breakpoint를 걸어 명시적 캐시 경계를 지정할 수 있다.
    """
    client = get_client()

    start = time.monotonic()
    try:
        response = await client.chat.completions.create(
            model=settings.azure_openai_deployment_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_schema", "json_schema": json_schema},
            extra_body={"prompt_cache_key": prompt_cache_key} if prompt_cache_key else None,
        )
    except APIError as exc:
        raise AzureOpenAIError(str(exc)) from exc
    elapsed_ms = (time.monotonic() - start) * 1000

    usage = response.usage
    cached_tokens = 0
    cache_write_tokens = 0
    if usage is not None and usage.prompt_tokens_details is not None:
        cached_tokens = usage.prompt_tokens_details.cached_tokens or 0
        cache_write_tokens = getattr(usage.prompt_tokens_details, "cache_write_tokens", None) or 0

    return StructuredCompletion(
        content=response.choices[0].message.content or "",
        model=response.model,
        prompt_tokens=usage.prompt_tokens if usage else 0,
        completion_tokens=usage.completion_tokens if usage else 0,
        cached_tokens=cached_tokens,
        cache_write_tokens=cache_write_tokens,
        elapsed_ms=elapsed_ms,
    )
