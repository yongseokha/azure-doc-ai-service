from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.config import settings


class AppException(Exception):
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code


class FileTooLargeError(AppException):
    def __init__(self, max_size_mb: int):
        super().__init__(f"파일 크기가 {max_size_mb}MB를 초과했습니다.", status_code=413)


class InvalidRequestError(AppException):
    def __init__(self, detail: str):
        super().__init__(detail, status_code=400)


class DocumentIntelligenceError(AppException):
    def __init__(self, detail: str):
        super().__init__(f"Document Intelligence 호출 중 오류가 발생했습니다: {detail}", status_code=502)


class FileStorageError(AppException):
    def __init__(self, detail: str):
        super().__init__(f"File Storage 처리 중 오류가 발생했습니다: {detail}", status_code=502)


class SearchIndexError(AppException):
    def __init__(self, detail: str):
        super().__init__(f"AI Search 인덱스 처리 중 오류가 발생했습니다: {detail}", status_code=502)


class StillProcessingError(AppException):
    def __init__(self):
        super().__init__("문서가 아직 처리 중입니다. 잠시 후 다시 시도해주세요.", status_code=202)


def _envelope(status_code: int, status_msg: str) -> dict:
    return {"statusCode": status_code, "statusMsg": status_msg, "result": None}


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=_envelope(exc.status_code, exc.message))


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content=_envelope(422, "요청 값이 올바르지 않습니다."))


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    message = str(exc) if settings.debug else "서버 내부 오류가 발생했습니다."
    return JSONResponse(status_code=500, content=_envelope(500, message))
