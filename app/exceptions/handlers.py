from fastapi import Request
from fastapi.responses import JSONResponse


class AppException(Exception):
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code


class UnsupportedFileTypeError(AppException):
    def __init__(self, filename: str):
        super().__init__(f"지원하지 않는 파일 형식입니다: {filename}", status_code=400)


class FileTooLargeError(AppException):
    def __init__(self, max_size_mb: int):
        super().__init__(f"파일 크기가 {max_size_mb}MB를 초과했습니다.", status_code=413)


class DocumentParsingError(AppException):
    def __init__(self, detail: str):
        super().__init__(f"문서 파싱 중 오류가 발생했습니다: {detail}", status_code=422)


class AzureOpenAIError(AppException):
    def __init__(self, detail: str):
        super().__init__(f"Azure OpenAI 호출 중 오류가 발생했습니다: {detail}", status_code=502)


class DocumentIntelligenceError(AppException):
    def __init__(self, detail: str):
        super().__init__(f"Document Intelligence 호출 중 오류가 발생했습니다: {detail}", status_code=502)


class BlobStorageError(AppException):
    def __init__(self, detail: str):
        super().__init__(f"Blob Storage 처리 중 오류가 발생했습니다: {detail}", status_code=502)


class BlobNotFoundError(AppException):
    def __init__(self, blob_name: str):
        super().__init__(f"Blob을 찾을 수 없습니다: {blob_name}", status_code=404)


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})
