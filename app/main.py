from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.config import settings
from app.exceptions.handlers import AppException, app_exception_handler

app = FastAPI(title=settings.app_name, debug=settings.debug)

app.add_exception_handler(AppException, app_exception_handler)
app.include_router(api_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
