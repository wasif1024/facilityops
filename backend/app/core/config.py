from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    # Without a user, libpq connects as the current OS user (local trust auth).
    database_url: str = "postgresql+psycopg://localhost:5432/facilityops"
    foundry_endpoint: str | None = Field(
        default=None,
        validation_alias=AliasChoices("FOUNDRY_PROJECT_ENDPOINT", "FOUNDRY_ENDPOINT"),
    )
    foundry_model: str = Field(
        default="gpt-5.2",
        validation_alias=AliasChoices("FOUNDRY_MODEL", "FOUNDRY_MODEL_DEPLOYMENT"),
    )
    azure_language_endpoint: str | None = None
    content_safety_endpoint: str | None = None
    frontend_origins: list[str] = ["http://localhost:5173"]
    allowed_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]
    max_request_body_bytes: int = Field(default=1_048_576, ge=1_024, le=10_485_760)
    auth_enabled: bool = False
    entra_tenant_id: str | None = None
    entra_api_audience: str | None = None
    # Role granted on first sign-in. Leave empty to give new users no access until
    # an administrator assigns a role.
    default_user_role: str | None = "tenant"
    rate_limit_requests: int = Field(default=30, ge=1)
    rate_limit_window_seconds: int = Field(default=60, ge=1)
    rate_limit_hmac_secret: str = "development-only-change-me"
    pii_mapping_encryption_key: str | None = None
    applicationinsights_connection_string: str | None = None
    minimum_classification_confidence: float = Field(default=0.75, ge=0, le=1)
    approval_priority_threshold: int = Field(default=4, ge=1, le=5)


@lru_cache
def get_settings() -> Settings:
    return Settings()
