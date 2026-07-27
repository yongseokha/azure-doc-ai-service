from fastapi import Request
from fastapi.responses import JSONResponse


class AppException(Exception):
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code


class FileTooLargeError(AppException):
    def __init__(self, max_size_mb: int):
        super().__init__(f"파일 크기가 {max_size_mb}MB를 초과했습니다.", status_code=413)


class DocumentIntelligenceError(AppException):
    def __init__(self, detail: str):
        super().__init__(f"Document Intelligence 호출 중 오류가 발생했습니다: {detail}", status_code=502)


class FileStorageError(AppException):
    def __init__(self, detail: str):
        super().__init__(f"File Storage 처리 중 오류가 발생했습니다: {detail}", status_code=502)


class SearchIndexError(AppException):
    def __init__(self, detail: str):
        super().__init__(f"AI Search 인덱스 처리 중 오류가 발생했습니다: {detail}", status_code=502)


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})
