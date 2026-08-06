from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.base import ApiRequest


class DocumentReference(BaseModel):
    hashKey: str = Field(examples=["a3f5c9d8e1b2..."], description="OCR 캐시 조회 키 (문서 해시)")
    termsName: str = Field(examples=["KT 요고 시리즈 이용약관"], description="표시용 약관명 (처리 로직에는 사용되지 않음)")


class TermsItem(BaseModel):
    itemNm: str = Field(examples=["이용 가능 고객"], description="검증/추출 대상 항목명")
    value: str | None = Field(default=None, examples=["개인, 미성년자, 외국인"], description="검증할 값. 없으면 약관에서 추출")
    desc: str | None = Field(default=None, description="itemNm 필드에 대한 설명")

    @field_validator("value", "desc", mode="before")
    @classmethod
    def _blank_as_none(cls, value: object) -> object:
        # 빈 문자열은 "값 없음"과 동일하게 취급한다. 문자열이 아닌 값은 그대로 흘려보내서
        # pydantic 자체의 타입 검증(422)이 처리하게 한다 (여기서 .strip()을 호출하면 안 됨).
        if isinstance(value, str) and value.strip() == "":
            return None
        return value


class TermsNameGroup(BaseModel):
    name: str = Field(examples=["요고 69"], description="검증 대상 상품명")
    items: list[TermsItem] = Field(min_length=1)


class TermsVerificationRequest(ApiRequest):
    documentHash: list[DocumentReference] = Field(min_length=1, description="검증에 쓰일 약관 문서 풀")
    names: list[TermsNameGroup] = Field(min_length=1, description="각 name의 items는 documentHash의 모든 문서와 교차 비교됨")
    callbackUrl: str = Field(description="처리 완료 후 결과를 전달할 콜백 URL")
    callbackParams: dict[str, Any] = Field(
        default_factory=dict,
        description="콜백 호출 시 결과 본문 최상위에 그대로 실어 보낼 임의의 파라미터 (예: knwlgInfold, termVrseq). 타입 제약 없이 그대로 echo됨",
        examples=[{"knwlgInfold": "...", "termVrseq": "..."}],
    )


class TermsItemResult(BaseModel):
    itemNm: str
    value: str | None
    status: Literal["MATCHED", "MISMATCH", "EXTRACTED", "NOT_FOUND"]
    llmValue: str | None = Field(default=None, description="약관 기준 확정값 (MISMATCH 시 정정값 포함)")
    evidence: str | None = Field(default=None, description="약관 원문 인용 (자동 검증되지 않은 참고용)")
    reason: str | None = Field(default=None, description="MISMATCH/NOT_FOUND일 때 설명")


class TermsDocumentItemsResult(BaseModel):
    hashKey: str
    termsName: str
    items: list[TermsItemResult]


class TermsNameResult(BaseModel):
    name: str
    documents: list[TermsDocumentItemsResult]


class UsageSummary(BaseModel):
    llmCallCount: int
    totalPromptTokens: int
    totalCompletionTokens: int
    totalCachedTokens: int
    totalElapsedMs: float
    avgElapsedMs: float


class TermsVerificationResult(BaseModel):
    names: list[TermsNameResult]
    usage: UsageSummary
