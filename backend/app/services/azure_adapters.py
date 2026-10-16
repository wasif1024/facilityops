from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

from backend.app.core.config import Settings
from backend.app.domain.intake import Classification, PiiRedaction


class AzureLanguagePiiRedactor:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def redact(self, text: str, language: str = "en") -> PiiRedaction:
        if not self._settings.azure_language_endpoint:
            raise RuntimeError("AZURE_LANGUAGE_ENDPOINT is not configured")
        if not self._settings.pii_mapping_encryption_key:
            raise RuntimeError("PII_MAPPING_ENCRYPTION_KEY is not configured")

        from azure.ai.textanalytics.aio import TextAnalyticsClient
        from azure.identity.aio import DefaultAzureCredential

        async with DefaultAzureCredential() as credential:
            async with TextAnalyticsClient(
                endpoint=self._settings.azure_language_endpoint,
                credential=credential,
            ) as client:
                results = await client.recognize_pii_entities(
                    [text],
                    language=language,
                    disable_service_logs=True,
                )

        if not results or getattr(results[0], "is_error", False):
            raise RuntimeError("Azure AI Language could not redact the request")

        entities = sorted(results[0].entities, key=lambda entity: entity.offset)
        redacted_text = text
        mapping: dict[str, str] = {}
        last_start = len(text)
        for index, entity in reversed(list(enumerate(entities, start=1))):
            start = entity.offset
            end = start + entity.length
            if end > last_start or start < 0 or end > len(text):
                raise RuntimeError(
                    "Azure AI Language returned overlapping or invalid PII offsets"
                )
            placeholder = f"[PII_{index:04d}]"
            redacted_text = redacted_text[:start] + placeholder + redacted_text[end:]
            mapping[placeholder] = entity.text
            last_start = start

        fernet = Fernet(self._settings.pii_mapping_encryption_key.encode("ascii"))
        encrypted_mapping = fernet.encrypt(
            json.dumps(mapping, ensure_ascii=True).encode("utf-8")
        )
        return PiiRedaction(
            redacted_text=redacted_text,
            encrypted_mapping=encrypted_mapping,
        )


class AzureContentSafetyInspector:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def is_safe(self, redacted_text: str) -> bool:
        if not self._settings.content_safety_endpoint:
            raise RuntimeError("CONTENT_SAFETY_ENDPOINT is not configured")

        from azure.ai.contentsafety.aio import ContentSafetyClient
        from azure.ai.contentsafety.models import (
            AnalyzeTextOptions,
            ShieldPromptOptions,
        )
        from azure.identity.aio import DefaultAzureCredential

        async with DefaultAzureCredential() as credential:
            async with ContentSafetyClient(
                self._settings.content_safety_endpoint, credential
            ) as client:
                text_result = await client.analyze_text(
                    AnalyzeTextOptions(text=redacted_text)
                )
                shield_result = await client.shield_prompt(
                    ShieldPromptOptions(user_prompt=redacted_text)
                )

        if text_result is None or shield_result is None:
            raise RuntimeError("Azure AI Content Safety returned an empty result")

        for category in text_result.categories_analysis or []:
            if category.severity >= 4:
                return False

        prompt_analysis: Any = getattr(shield_result, "user_prompt_analysis", None)
        if prompt_analysis is None:
            raise RuntimeError("Prompt Shields did not return user prompt analysis")
        if getattr(prompt_analysis, "attack_detected", None) is None:
            raise RuntimeError("Prompt Shields result is missing attack status")
        return not prompt_analysis.attack_detected


class FoundryMaintenanceClassifier:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._instructions = (
            Path(__file__)
            .parents[1]
            .joinpath("prompts", "maintenance_triage_classifier.md")
            .read_text(encoding="utf-8")
        )

    async def classify(self, redacted_text: str) -> Classification:
        if not self._settings.foundry_endpoint:
            raise RuntimeError("FOUNDRY_ENDPOINT is not configured")

        from agent_framework import Agent
        from agent_framework.foundry import FoundryChatClient
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential()
        try:
            agent = Agent(
                client=FoundryChatClient(
                    project_endpoint=self._settings.foundry_endpoint,
                    model=self._settings.foundry_model,
                    credential=credential,
                ),
                name="Maintenance Triage Classifier",
                description="Classifies redacted maintenance requests without tools or side effects.",
                instructions=self._instructions,
                default_options={"store": False},
            )
            async with agent:
                response = await agent.run(
                    redacted_text,
                    options={"response_format": Classification},
                )
            if isinstance(response.value, Classification):
                return response.value
            if isinstance(response.value, dict):
                return Classification.model_validate(response.value)
            raise RuntimeError("Foundry returned no valid structured classification")
        finally:
            credential.close()
