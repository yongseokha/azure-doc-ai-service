from fastapi import APIRouter

from app.api.v1.endpoints import documents, terms

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(documents.router)
api_router.include_router(terms.router)
