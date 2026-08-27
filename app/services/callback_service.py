import asyncio
import json
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


async def _post_with_retry(url: str, **kwargs) -> CallbackResult:
    """콜백 URL로 POST하고 응답을 해석한다.

    응답 본문은 {"result": 0/1, "message": ..., "data": ...} 형태이며, result=0이 진짜 성공이다.
    - HTTP 레벨 에러(타임아웃/5xx 등)만 backoff와 함께 재시도한다.
    - HTTP는 정상(2xx)인데 result != 0(비즈니스 레벨 실패)인 경우는 재시도하지 않고
      실패로 기록만 한다 - 전달 자체는 성공했으므로 반복 전송할 이유가 없다.
    """
    client = get_client()

    for attempt in range(MAX_ATTEMPTS):
        request = client.build_request("POST", url, **kwargs)
        logger.info("콜백 요청 헤더: url=%s content-type=%s", url, request.headers.get("content-type"))
        try:
            response = await client.send(request)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("콜백 호출 실패 (%d/%d): url=%s error=%s", attempt + 1, MAX_ATTEMPTS, url, exc)
            if attempt < MAX_ATTEMPTS - 1:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS[attempt])
            continue

        logger.info("콜백 응답: url=%s status=%s body=%s", url, response.status_code, response.text)

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


async def send_callback(url: str, payload: dict) -> CallbackResult:
    """콜백 URL로 JSON 바디를 전송한다."""
    logger.info(
        "콜백 전송(JSON): url=%s payload=%s",
        url,
        json.dumps(payload, ensure_ascii=False, default=str),
    )
    return await _post_with_retry(url, json=payload)


async def send_callback_multipart(
    url: str, data: dict, file: tuple[str, bytes, str] | None = None
) -> CallbackResult:
    """콜백 URL로 multipart/form-data를 전송한다.

    최상단은 data와 file 두 파트뿐이다. data는 JSON 문자열로 직렬화되어 Content-Type이
    application/json인 파트로 실리고(knwlgInfoId/termVrfSeq/vrfDataResltJson/errSbst는
    모두 그 안에 중첩된다), file은 (filename, content, content_type) 튜플로 진짜 바이너리
    파트가 된다. data 파트는 filename 없이 (None, ...) 형태로 넘겨야 받는 쪽(예: Spring의
    @RequestPart로 객체 역직렬화)이 이 파트를 JSON으로 인식한다 - 일반 폼 필드로 보내면
    Content-Type이 안 붙어서 역직렬화가 안 될 수 있다. file이 없어도 항상 multipart로
    나가도록 빈 파일 파트를 채운다 - httpx는 files가 비어있으면
    application/x-www-form-urlencoded로 인코딩해버려서, 요청마다 Content-Type이 달라지는 걸
    막으려면 file 파트 자체는 항상 있어야 한다.
    """
    data_json = json.dumps(data, ensure_ascii=False)
    file_desc = f"{file[0]} ({len(file[1])} bytes, {file[2]})" if file else "(없음)"
    logger.info("콜백 전송(multipart): url=%s data=%s file=%s", url, data_json, file_desc)

    files = {
        "data": (None, data_json, "application/json"),
        "file": file or ("", b"", "application/octet-stream"),
    }
    return await _post_with_retry(url, files=files)
