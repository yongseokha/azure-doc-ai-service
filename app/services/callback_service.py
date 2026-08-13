import asyncio
import logging
from dataclasses import dataclass
from functools import lru_cache

import httpx

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = [1, 2, 4]
CALLBACK_TIMEOUT_SECONDS = 10.0


@dataclass
class CallbackResult:
    success: bool
    message: str | None = None


@lru_cache
def get_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=CALLBACK_TIMEOUT_SECONDS)


async def close() -> None:
    if get_client.cache_info().currsize == 0:
        return
    await get_client().aclose()
    get_client.cache_clear()


async def send_callback(url: str, payload: dict) -> CallbackResult:
    """콜백 URL로 결과를 전송한다.

    응답 본문은 {"result": 0/1, "message": ..., "data": ...} 형태이며, result=0이 진짜 성공이다.
    - HTTP 레벨 에러(타임아웃/5xx 등)만 backoff와 함께 재시도한다.
    - HTTP는 정상(2xx)인데 result != 0(비즈니스 레벨 실패)인 경우는 재시도하지 않고
      실패로 기록만 한다 - 전달 자체는 성공했으므로 반복 전송할 이유가 없다.
    """
    client = get_client()

    for attempt in range(MAX_ATTEMPTS):
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("콜백 호출 실패 (%d/%d): url=%s error=%s", attempt + 1, MAX_ATTEMPTS, url, exc)
            if attempt < MAX_ATTEMPTS - 1:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS[attempt])
            continue

        try:
            body = response.json()
        except ValueError:
            logger.warning("콜백 응답을 JSON으로 해석할 수 없음: url=%s", url)
            return CallbackResult(success=False, message=None)

        message = body.get("message")
        if body.get("result") == 0:
            return CallbackResult(success=True, message=message)

        logger.warning("콜백 처리 실패 응답: url=%s result=%s message=%s", url, body.get("result"), message)
        return CallbackResult(success=False, message=message)

    logger.error("콜백 최종 실패: url=%s", url)
    return CallbackResult(success=False, message=None)
