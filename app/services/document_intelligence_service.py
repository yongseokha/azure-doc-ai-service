import asyncio
import re
from functools import lru_cache

from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeResult, DocumentContentFormat
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError

from app.core.config import settings
from app.exceptions.handlers import DocumentAnalysisTimeoutError, DocumentIntelligenceError

DEFAULT_MODEL_ID = "prebuilt-layout"

_PAGE_NUMBER_MARKER_RE = re.compile(r'<!-- PageNumber="[^"]*" -->\n*')
_PAGE_BREAK_MARKER = "<!-- PageBreak -->"


def renumber_pages(markdown: str) -> str:
    """OCR이 인식한 인쇄 페이지 번호(PageNumber)는 페이지 하단에 붙어 있고 인식 실패도 잦아서,
    각 페이지 본문 맨 위에 PageBreak 개수 기준의 물리적 페이지 순번을 다시 붙인다."""
    text = _PAGE_NUMBER_MARKER_RE.sub("", markdown)
    pages = text.split(_PAGE_BREAK_MARKER)
    numbered_pages = [
        f'<!-- PageNumber="{i}" -->\n{page.strip(chr(10))}' for i, page in enumerate(pages, start=1)
    ]
    return f"\n{_PAGE_BREAK_MARKER}\n".join(numbered_pages)


@lru_cache
def get_document_intelligence_client() -> DocumentIntelligenceClient:
    return DocumentIntelligenceClient(
        endpoint=settings.azure_document_intelligence_endpoint,
        credential=AzureKeyCredential(settings.azure_document_intelligence_api_key),
    )


async def close() -> None:
    if get_document_intelligence_client.cache_info().currsize == 0:
        return
    await get_document_intelligence_client().close()
    get_document_intelligence_client.cache_clear()


async def analyze_document(content: bytes, model_id: str = DEFAULT_MODEL_ID) -> AnalyzeResult:
    client = get_document_intelligence_client()

    try:
        poller = await client.begin_analyze_document(
            model_id,
            body=content,
            content_type="application/octet-stream",
            output_content_format=DocumentContentFormat.MARKDOWN,
        )
        return await asyncio.wait_for(
            poller.result(), timeout=settings.document_intelligence_timeout_seconds
        )
    except HttpResponseError as exc:
        raise DocumentIntelligenceError(str(exc)) from exc
    except asyncio.TimeoutError as exc:
        raise DocumentAnalysisTimeoutError(settings.document_intelligence_timeout_seconds) from exc
