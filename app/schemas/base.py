from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ApiRequest(BaseModel, Generic[T]):
    userId: str = Field(default="", description="사용자 사번")
    infId: str = Field(default="", description="인터페이스 ID (API별 고정값)")
    rqtKey: str = Field(default="", description="요청키 (클라이언트 생성 랜덤 키)")


class ApiResponse(BaseModel, Generic[T]):
    statusCode: int = Field(description="HTTP 상태코드")
    statusMsg: str = Field(description="HTTP 상태메시지")
    result: T | None = Field(default=None, description="결과 오브젝트 (내부 형식 자율)")
