from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiRequest(BaseModel, Generic[T]):
    userId: str = ""
    infId: str = ""
    rqtKey: str = ""


class ApiResponse(BaseModel, Generic[T]):
    statusCode: int
    statusMsg: str
    result: T | None = None
