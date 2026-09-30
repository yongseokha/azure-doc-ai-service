from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.base import ApiRequest
from app.utils.common import is_personal_information


def _reject_personal_information(value: str | None) -> str | None:
    if is_personal_information(value or ""):
        raise ValueError("개인정보로 의심되는 값은 입력할 수 없습니다.")
    return value


class DocumentReference(BaseModel):
    ocrResltKey: str = Field(examples=["a3f5c9d8e1b2..."], description="OCR 캐시 조회 키 (문서 해시)")
    termNm: str = Field(examples=["KT 요고 시리즈 이용약관"], description="표시용 약관명 (처리 로직에는 사용되지 않음)")
    aplyDate: str = Field(examples=["2026-01-01"], description="약관 시행일 (처리 로직에는 사용되지 않음, 보관/표시용)")

    @field_validator("termNm", "aplyDate", mode="after")
    @classmethod
    def _no_personal_information(cls, value: str) -> str:
        return _reject_personal_information(value)


class TermsItem(BaseModel):
    itemNm: str = Field(examples=["이용 가능 고객"], description="검증/추출 대상 항목명")
    value: str | None = Field(default=None, examples=["개인, 미성년자, 외국인"], description="검증할 값. 없으면 약관에서 추출")
    desc: str | None = Field(default=None, description="itemNm 필드에 대한 설명")

    @field_validator("itemNm", mode="after")
    @classmethod
    def _no_personal_information_itemnm(cls, value: str) -> str:
        return _reject_personal_information(value)

    @field_validator("value", "desc", mode="after")
    @classmethod
    def _blank_as_none(cls, value: str | None) -> str | None:
        # mode="after"라 이 시점엔 value가 이미 str | None으로 타입 검증이 끝난 뒤다
        # (문자열이 아닌 입력은 여기 도달하기 전에 pydantic이 422로 걸러낸다).
        # 빈 문자열은 "값 없음"과 동일하게 취급한다.
        if value is not None and value.strip() == "":
            return None
        return value

    @field_validator("value", "desc", mode="after")
    @classmethod
    def _no_personal_information(cls, value: str | None) -> str | None:
        return _reject_personal_information(value)


class TermsNameGroup(BaseModel):
    name: str = Field(examples=["요고 69"], description="검증 대상 상품명")
    items: list[TermsItem] = Field(min_length=1)

    @field_validator("name", mode="after")
    @classmethod
    def _no_personal_information(cls, value: str) -> str:
        return _reject_personal_information(value)


class TermsVerificationRequest(ApiRequest):
    termInfo: list[DocumentReference] = Field(min_length=1, description="검증에 쓰일 약관 문서 풀")
    data: list[TermsNameGroup] = Field(min_length=1, description="각 name의 items는 termInfo의 모든 문서와 교차 비교됨")
    knwlgInfoId: int = Field(description="지식 정보 ID (콜백 본문에 받은 그대로 실려감)")
    termVrfSeq: int = Field(description="약관 버전 순번 (콜백 본문에 받은 그대로 실려감)")
    knwlgNm: str = Field(examples=["KT 요고 시리즈"], description="지식명 (콜백/리포트에 그대로 표시됨)")

    @field_validator("knwlgNm", mode="after")
    @classmethod
    def _no_personal_information(cls, value: str) -> str:
        return _reject_personal_information(value)


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
    matchRate: int | None = Field(
        default=None, description="현재 값과 llmValue의 의미적 일치율 (0~100). reason이 null이거나 llmValue가 null이면 null"
    )


class TermsDocumentItemsResult(BaseModel):
    ocrResltKey: str
    termNm: str
    aplyDate: str | None = Field(default=None, description="약관 시행일. 이 필드 추가 이전에 저장된 결과는 null")
    nameMatchRate: int | None = Field(
        default=None, description="대상명과 이 문서 내 실제 표현의 의미적 일치율 (0~100). 언급 자체가 없으면 0. 이 필드 추가 이전에 저장된 결과는 null"
    )
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
    totalCacheWriteTokens: int
    totalElapsedSeconds: float
    avgElapsedSeconds: float


class TermsVerificationResult(BaseModel):
    knwlgNm: str | None = Field(default=None, description="지식명. 이 필드 추가 이전에 저장된 결과는 null")
    data: list[TermsNameResult]


# ---- 검증 원문 기반 약관 검증 (/terms/verify-ask) ----


class SourceItem(BaseModel):
    itemNm: str = Field(examples=["초이스 상품여부"], description="검증 원문의 항목명")
    value: str | None = Field(default=None, examples=["Y"], description="검증 원문의 항목값")

    @field_validator("itemNm", mode="after")
    @classmethod
    def _no_personal_information_itemnm(cls, value: str) -> str:
        return _reject_personal_information(value)

    @field_validator("value", mode="after")
    @classmethod
    def _blank_as_none(cls, value: str | None) -> str | None:
        # 빈 문자열은 "값 없음"과 동일하게 취급한다.
        if value is not None and value.strip() == "":
            return None
        return value

    @field_validator("value", mode="after")
    @classmethod
    def _no_personal_information(cls, value: str | None) -> str | None:
        return _reject_personal_information(value)


class SourceGroup(BaseModel):
    name: str = Field(examples=["요고 69"], description="검증 대상 상품명")
    items: list[SourceItem] = Field(min_length=1, description="검증 원문 항목 목록 (askValue 추출 대상)")

    @field_validator("name", mode="after")
    @classmethod
    def _no_personal_information(cls, value: str) -> str:
        return _reject_personal_information(value)


class VerificationTarget(BaseModel):
    itemNm: str = Field(examples=["대상 고객"], description="검증 항목명")
    desc: str | None = Field(default=None, description="검증 항목에 대한 설명")

    @field_validator("itemNm", mode="after")
    @classmethod
    def _no_personal_information_itemnm(cls, value: str) -> str:
        return _reject_personal_information(value)

    @field_validator("desc", mode="after")
    @classmethod
    def _blank_as_none(cls, value: str | None) -> str | None:
        if value is not None and value.strip() == "":
            return None
        return value

    @field_validator("desc", mode="after")
    @classmethod
    def _no_personal_information(cls, value: str | None) -> str | None:
        return _reject_personal_information(value)


class AskDocumentReference(DocumentReference):
    vrfItem: list[VerificationTarget] = Field(min_length=1, description="이 약관 문서로 검증할 항목 목록")


class TermsAskVerificationRequest(ApiRequest):
    termInfo: list[AskDocumentReference] = Field(min_length=1, description="약관 문서와 문서별 검증 항목")
    data: list[SourceGroup] = Field(
        min_length=1, description="검증 원문. 각 name마다 termInfo 문서별로 그 문서의 vrfItem 전체가 비교됨"
    )
    knwlgInfoId: int = Field(description="지식 정보 ID (콜백 본문에 받은 그대로 실려감)")
    termVrfSeq: int = Field(description="약관 버전 순번 (콜백 본문에 받은 그대로 실려감)")
    knwlgNm: str = Field(examples=["KT 요고 시리즈"], description="지식명 (콜백/리포트에 그대로 표시됨)")

    @field_validator("knwlgNm", mode="after")
    @classmethod
    def _no_personal_information(cls, value: str) -> str:
        return _reject_personal_information(value)

    @field_validator("termInfo", mode="after")
    @classmethod
    def _no_duplicate_documents(cls, value: list[AskDocumentReference]) -> list[AskDocumentReference]:
        # 문서마다 검증 항목이 달라서, 같은 문서가 두 번 오면 어느 쪽 vrfItem을 쓸지 정할 수 없다.
        keys = [ref.ocrResltKey for ref in value]
        if len(keys) != len(set(keys)):
            raise ValueError("termInfo에 같은 ocrResltKey가 중복되어 있습니다. 문서당 한 번만 보내고 vrfItem을 합쳐주세요.")
        return value


class TermsAskItemResult(BaseModel):
    itemNm: str = Field(description="검증 항목명 (vrfItem.itemNm)")
    askValue: str | None = Field(default=None, description="검증 원문에서 찾은 값. 검증 원문에 해당 항목 자체가 없으면 null")
    llmValue: str | None = Field(default=None, description="약관 원문에서 찾은 값. 약관에 해당 항목 자체가 없으면 null")
    evidence: str | None = Field(default=None, description="약관 원문 인용 (자동 검증되지 않은 참고용). llmValue가 null이면 null")
    page: int | None = Field(default=None, description="evidence가 위치한 페이지 번호 (자동 검증되지 않은 참고용)")
    article: str | None = Field(
        default=None, description="evidence가 위치한 약관 조항 번호, 예: '제3조', '제3조 2항' (자동 검증되지 않은 참고용)"
    )
    reason: str | None = Field(default=None, description="askValue와 llmValue의 차이 설명. 완전히 같으면 null")
    matchRate: int | None = Field(
        default=None, description="askValue와 llmValue의 의미적 일치율 (0~100). reason이 null이거나 llmValue가 null이면 null"
    )
    status: Literal["MATCHED", "PARTIAL_MATCH", "MISMATCH"]


class TermsAskDocumentItemsResult(BaseModel):
    ocrResltKey: str
    termNm: str
    aplyDate: str | None = None
    nameMatchRate: int | None = Field(default=None, description="대상명과 이 문서 내 실제 표현의 의미적 일치율 (0~100)")
    items: list[TermsAskItemResult]


class TermsAskNameResult(BaseModel):
    name: str
    documents: list[TermsAskDocumentItemsResult]


class TermsAskVerificationResult(BaseModel):
    knwlgNm: str | None = None
    data: list[TermsAskNameResult]
