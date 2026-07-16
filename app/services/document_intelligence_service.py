from functools import lru_cache

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeResult, DocumentContentFormat
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError

from app.core.config import settings
from app.exceptions.handlers import DocumentIntelligenceError

DEFAULT_MODEL_ID = "prebuilt-layout"


@lru_cache
def get_document_intelligence_client() -> DocumentIntelligenceClient:
    return DocumentIntelligenceClient(
        endpoint=settings.azure_document_intelligence_endpoint,
        credential=AzureKeyCredential(settings.azure_document_intelligence_api_key),
    )


def analyze_document(content: bytes, model_id: str = DEFAULT_MODEL_ID) -> AnalyzeResult:
    client = get_document_intelligence_client()

    try:
        poller = client.begin_analyze_document(
            model_id,
            body=content,
            content_type="application/octet-stream",
            output_content_format=DocumentContentFormat.MARKDOWN,
        )
        return poller.result()
    except HttpResponseError as exc:
        raise DocumentIntelligenceError(str(exc)) from exc


def extract_markdown(content: bytes, model_id: str = DEFAULT_MODEL_ID) -> str:
    result = analyze_document(content, model_id=model_id)
    return result.content or ""
