from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.api.v1.router import api_router
from app.core.config import settings
from app.exceptions.handlers import (
    AppException,
    app_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.services import document_intelligence_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        yield
    finally:
        await document_intelligence_service.close()


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

app.add_exception_handler(AppException, app_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)
app.include_router(api_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
