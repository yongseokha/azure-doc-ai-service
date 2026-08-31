from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.base import ApiRequest


class DocumentReference(BaseModel):
    ocrResltKey: str = Field(examples=["a3f5c9d8e1b2..."], description="OCR 캐시 조회 키 (문서 해시)")
    termNm: str = Field(examples=["KT 요고 시리즈 이용약관"], description="표시용 약관명 (처리 로직에는 사용되지 않음)")
    aplyDate: str = Field(examples=["2026-01-01"], description="약관 시행일 (처리 로직에는 사용되지 않음, 보관/표시용)")


class TermsItem(BaseModel):
    itemNm: str = Field(examples=["이용 가능 고객"], description="검증/추출 대상 항목명")
    value: str | None = Field(default=None, examples=["개인, 미성년자, 외국인"], description="검증할 값. 없으면 약관에서 추출")
    desc: str | None = Field(default=None, description="itemNm 필드에 대한 설명")

    @field_validator("value", "desc", mode="after")
    @classmethod
    def _blank_as_none(cls, value: str | None) -> str | None:
        # mode="after"라 이 시점엔 value가 이미 str | None으로 타입 검증이 끝난 뒤다
        # (문자열이 아닌 입력은 여기 도달하기 전에 pydantic이 422로 걸러낸다).
        # 빈 문자열은 "값 없음"과 동일하게 취급한다.
        if value is not None and value.strip() == "":
            return None
        return value


class TermsNameGroup(BaseModel):
    name: str = Field(examples=["요고 69"], description="검증 대상 상품명")
    items: list[TermsItem] = Field(min_length=1)


class TermsVerificationRequest(ApiRequest):
    termInfo: list[DocumentReference] = Field(min_length=1, description="검증에 쓰일 약관 문서 풀")
    data: list[TermsNameGroup] = Field(min_length=1, description="각 name의 items는 termInfo의 모든 문서와 교차 비교됨")
    knwlgInfoId: int = Field(description="지식 정보 ID (콜백 본문에 받은 그대로 실려감)")
    termVrfSeq: int = Field(description="약관 버전 순번 (콜백 본문에 받은 그대로 실려감)")
    knwlgNm: str = Field(examples=["KT 요고 시리즈"], description="지식명 (콜백/리포트에 그대로 표시됨)")


class TermsItemResult(BaseModel):
    itemNm: str
    value: str | None
    subClaim: str | None = Field(default=None, description="value가 여러 조건으로 분해된 경우, 그중 하나의 조건 (분해 안 됐으면 null)")
    status: Literal["MATCHED", "PARTIAL_MATCH", "MISMATCH"]
    llmValue: str | None = Field(
        default=None, description="약관 기준 확정값. value가 없어도 약관에서 찾아 채움. 약관에 해당 내용 자체가 없으면 null"
    )
    evidence: str | None = Field(default=None, description="약관 원문 인용 (자동 검증되지 않은 참고용). 약관에 해당 내용 자체가 없으면 null")
    page: int | None = Field(default=None, description="evidence가 위치한 페이지 번호 (자동 검증되지 않은 참고용)")
    article: str | None = Field(
        default=None, description="evidence가 위치한 약관 조항 번호, 예: '제3조', '제3조 2항' (자동 검증되지 않은 참고용)"
    )
    reason: str | None = Field(default=None, description="PARTIAL_MATCH/MISMATCH일 때 설명. MATCHED 시 보통 null")


class TermsDocumentItemsResult(BaseModel):
    ocrResltKey: str
    termNm: str
    aplyDate: str | None = Field(default=None, description="약관 시행일. 이 필드 추가 이전에 저장된 결과는 null")
    items: list[TermsItemResult]


class TermsNameResult(BaseModel):
    name: str
    documents: list[TermsDocumentItemsResult]


class UsageSummary(BaseModel):
    model: str = Field(description="실제 응답에 사용된 모델 (Azure OpenAI 응답의 model 필드)")
    llmCallCount: int
    totalPromptTokens: int
    totalCompletionTokens: int
    totalCachedTokens: int
    totalElapsedSeconds: float
    avgElapsedSeconds: float


class TermsVerificationResult(BaseModel):
    knwlgNm: str | None = Field(default=None, description="지식명. 이 필드 추가 이전에 저장된 결과는 null")
    data: list[TermsNameResult]
