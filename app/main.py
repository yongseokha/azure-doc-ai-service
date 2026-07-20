from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.config import settings
from app.exceptions.handlers import AppException, app_exception_handler
from app.services import azure_openai_service, blob_storage_service, document_intelligence_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await blob_storage_service.ensure_container_exists()
        yield
    finally:
        await blob_storage_service.close()
        await azure_openai_service.close()
        await document_intelligence_service.close()


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

app.add_exception_handler(AppException, app_exception_handler)
app.include_router(api_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
