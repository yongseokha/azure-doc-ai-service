from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class BaseRequest(BaseModel):
    userId: str = Field(..., description="사용자 사번")
    rqtKey: str = Field(..., description="요청키 (클라이언트 생성 랜덤 키)")
    infId: str = Field(..., description="인터페이스 ID (API별 고정값)")


class ApiResponse(BaseModel, Generic[T]):
    statusCode: int
    statusMsg: str
    result: T


def success_response(result: T, status_code: int = 200, status_msg: str = "OK") -> ApiResponse[T]:
    return ApiResponse(statusCode=status_code, statusMsg=status_msg, result=result)
