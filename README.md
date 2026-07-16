# azure-doc-ai-service

FastAPI 기반 문서 AI 서비스. Azure OpenAI(GPT-5), Azure Document Intelligence, Azure Blob Storage를 사용합니다.

## 프로젝트 구조

```
app/
├── main.py                  # FastAPI 앱 엔트리포인트
├── core/
│   └── config.py             # 환경변수 기반 설정 (pydantic-settings)
├── api/v1/
│   ├── router.py             # /api/v1 라우터 집합
│   └── endpoints/
│       ├── chat.py           # Azure OpenAI 채팅 완성
│       ├── documents.py      # 문서 파싱(로컬/DI)·요약
│       └── storage.py        # Blob Storage 업로드/다운로드/조회/삭제
├── schemas/                  # Pydantic 요청/응답 모델
├── services/
│   ├── azure_openai_service.py
│   ├── document_intelligence_service.py  # Azure DI(prebuilt-layout) 분석
│   ├── document_parser_service.py        # 로컬 pypdf/docx 파싱
│   └── blob_storage_service.py           # Azure Blob Storage CRUD
└── exceptions/
    └── handlers.py            # 공통 예외 및 핸들러

data/
├── sample/                   # 원본 테스트 문서
└── parsed/                   # 파서별 파싱 결과 비교 산출물

notebooks/                    # Azure 리소스 연동 확인용 테스트 노트북
```

## 로컬 실행

```bash
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env   # 값 채우기
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

## Docker 실행

```bash
docker compose up --build
```

`.env` 파일이 있어야 하며(`env_file: .env`), 코드 변경 시 `--reload`로 즉시 반영됩니다.

## 환경변수

`.env.example` 참고. Azure OpenAI / Document Intelligence / Blob Storage 리소스의 엔드포인트·키·연결 문자열이 필요합니다.

## API

- `POST /api/v1/documents/parse` — 로컬 라이브러리(pypdf/docx)로 파싱
- `POST /api/v1/documents/parse-di` — Azure Document Intelligence로 파싱
- `POST /api/v1/documents/summarize` — 파싱 후 Azure OpenAI로 요약
- `POST /api/v1/chat/completion` — Azure OpenAI 채팅 완성
- `POST /api/v1/storage/upload` — Blob 업로드
- `GET /api/v1/storage` — Blob 목록 조회
- `GET /api/v1/storage/{blob_name}` — Blob 다운로드
- `DELETE /api/v1/storage/{blob_name}` — Blob 삭제
- `GET /health` — 헬스 체크
