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
    # Photo captions/OCR and voice transcription. Usually the same AIServices resource.
    vision_endpoint: str | None = None
    speech_endpoint: str | None = None
    # Scanned documents (Document Intelligence read model). Also usually the same resource.
    document_intelligence_endpoint: str | None = None
    # Azure AI Translator: requests are translated into the staff language for reviewers (FR-60).
    # The same AI Services resource works. Unset: no translation, originals only.
    translator_endpoint: str | None = None
    staff_language: str = Field(default="en", pattern=r"^[a-z]{2}$")
    frontend_origins: list[str] = ["http://localhost:5173"]
    allowed_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]
    # Large enough for an 8 MiB photo or voice note sent as base64 JSON.
    max_request_body_bytes: int = Field(default=12_582_912, ge=1_024, le=16_777_216)
    auth_enabled: bool = False
    # Staff sign in with the workforce Entra ID tenant.
    entra_tenant_id: str | None = None
    entra_api_audience: str | None = None
    # Staff get no role on first sign-in; an administrator assigns one.
    entra_workforce_default_role: str | None = None
    # Residents sign up and sign in with Entra External ID (https://<subdomain>.ciamlogin.com).
    entra_external_tenant_id: str | None = None
    entra_external_tenant_subdomain: str | None = None
    entra_external_api_audience: str | None = None
    # Role granted on first sign-in to residents (External ID) and development identities.
    # Leave empty to give new users no access until an administrator assigns a role.
    default_user_role: str | None = "tenant"
    # Demo accounts (alice, fm, tech, local-developer) created by migration 0004 for local
    # development. Never created when APP_ENV=production. Remove: scripts/db.sh remove-demo-accounts.
    seed_demo_accounts: bool = True
    # Transparency. What was sent to each model and what it answered is stored (redacted text
    # only) for people who can read requests, and purged after this many days.
    capture_model_content: bool = True
    model_content_retention_days: int = Field(default=90, ge=1)
    # Decision trail and audit records are kept this long (spec section 8: two years).
    audit_retention_days: int = Field(default=730, ge=30)
    # Application logs: "json" (one object per line, for Azure Monitor) or "text" (local reading).
    log_format: Literal["json", "text"] = "text"
    log_level: str = "INFO"
    rate_limit_requests: int = Field(default=30, ge=1)
    rate_limit_window_seconds: int = Field(default=60, ge=1)
    rate_limit_hmac_secret: str = "development-only-change-me"
    pii_mapping_encryption_key: str | None = None
    applicationinsights_connection_string: str | None = None
    # Used only when confirmation is disabled (CLASSIFICATION_CONFIRMATIONS=0).
    minimum_classification_confidence: float = Field(default=0.75, ge=0, le=1)
    # Borderline band: answers in [low, high) are re-classified and must agree to be automated.
    classification_band_low: float = Field(default=0.70, ge=0, le=1)
    classification_band_high: float = Field(default=0.80, ge=0, le=1)
    classification_confirmations: int = Field(default=2, ge=0, le=4)
    approval_priority_threshold: int = Field(default=4, ge=1, le=5)


@lru_cache
def get_settings() -> Settings:
    return Settings()
