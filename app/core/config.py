from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "Document AI Service"
    debug: bool = False

    azure_document_intelligence_endpoint: str
    azure_document_intelligence_api_key: str

    azure_search_endpoint: str
    azure_search_api_key: str
    azure_search_index_name: str = "ocr-documents"
    azure_search_terms_index_name: str = "terms-verification-jobs"

    azure_file_storage_connection_string: str
    azure_file_share_name: str = "ocr-archive"

    azure_openai_endpoint: str
    azure_openai_api_key: str
    azure_openai_deployment_name: str
    azure_openai_api_version: str = "2024-12-01-preview"
    azure_openai_timeout_seconds: float = 60.0
    terms_verification_concurrency: int = 5

    max_upload_size_mb: int = 20
    document_intelligence_timeout_seconds: int = 300
    document_intelligence_callback_url: str

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


settings = Settings()
