import asyncio
import logging
from functools import lru_cache

import httpx

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = [1, 2, 4]
CALLBACK_TIMEOUT_SECONDS = 10.0


@lru_cache
def get_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=CALLBACK_TIMEOUT_SECONDS)


async def close() -> None:
    if get_client.cache_info().currsize == 0:
        return
    await get_client().aclose()
    get_client.cache_clear()


async def send_callback(url: str, payload: dict) -> bool:
    """콜백 URL로 결과를 전송한다. 실패 시 backoff와 함께 재시도하고,
    모두 실패하면 False를 반환한다 (예외를 던지지 않음 — 호출자가 job 상태에 반영)."""
    client = get_client()

    for attempt in range(MAX_ATTEMPTS):
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            logger.warning("콜백 호출 실패 (%d/%d): url=%s error=%s", attempt + 1, MAX_ATTEMPTS, url, exc)
            if attempt < MAX_ATTEMPTS - 1:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS[attempt])

    logger.error("콜백 최종 실패: url=%s", url)
    return False
