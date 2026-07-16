from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "Document AI Service"
    debug: bool = False

    azure_openai_endpoint: str
    azure_openai_api_key: str
    azure_openai_deployment_name: str
    azure_openai_api_version: str = "2024-12-01-preview"

    azure_document_intelligence_endpoint: str
    azure_document_intelligence_api_key: str

    azure_storage_connection_string: str
    azure_storage_container_name: str = "documents"

    max_upload_size_mb: int = 20

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


settings = Settings()
