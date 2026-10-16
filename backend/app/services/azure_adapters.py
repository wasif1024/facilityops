from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

from backend.app.core.config import Settings
from backend.app.domain.intake import Classification, PiiRedaction, RequestSource
from backend.app.services.agent_monitoring import (
    AgentMonitoringMiddleware,
    AgentRunRecorder,
)


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


_COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"


class AzureMediaTranscriber:
    """Turns photos and voice notes into text with Azure AI Vision and Speech.

    Photos: Image Analysis 4.0 caption (gender-neutral) plus OCR. Voice: Speech fast
    transcription. Media goes only to these services, never to a language model, and
    is not stored; the resulting text then goes through PII redaction like typed text.
    """

    _VISION_API_VERSION = "2024-02-01"
    _SPEECH_API_VERSION = "2024-11-15"
    _MIN_CAPTION_CONFIDENCE = 0.5
    _LOCALES = {"en": "en-US", "es": "es-ES", "fr": "fr-FR"}

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def transcribe(
        self,
        *,
        source: RequestSource,
        media: bytes,
        media_type: str,
        language: str | None,
    ) -> str:
        if source is RequestSource.PHOTO:
            return await self._describe_photo(media)
        if source is RequestSource.VOICE:
            return await self._transcribe_voice(media, media_type, language)
        raise ValueError(f"No transcription for source {source}")

    async def _token(self) -> str:
        from azure.identity.aio import DefaultAzureCredential

        async with DefaultAzureCredential() as credential:
            return (await credential.get_token(_COGNITIVE_SERVICES_SCOPE)).token

    async def _describe_photo(self, media: bytes) -> str:
        if not self._settings.vision_endpoint:
            raise RuntimeError("VISION_ENDPOINT is not configured")
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self._settings.vision_endpoint.rstrip('/')}/computervision/imageanalysis:analyze",
                params={
                    "api-version": self._VISION_API_VERSION,
                    "features": "caption,read",
                    "gender-neutral-caption": "true",
                },
                headers={
                    "Authorization": f"Bearer {await self._token()}",
                    "Content-Type": "application/octet-stream",
                },
                content=media,
            )
        response.raise_for_status()
        result = response.json()

        parts = []
        caption = result.get("captionResult") or {}
        if caption.get("text") and caption.get("confidence", 0) >= self._MIN_CAPTION_CONFIDENCE:
            parts.append(f"Photo shows: {caption['text']}.")
        lines = [
            line["text"]
            for block in (result.get("readResult") or {}).get("blocks", [])
            for line in block.get("lines", [])
        ]
        if lines:
            parts.append("Text in photo: " + " ".join(lines))
        return " ".join(parts)

    async def _transcribe_voice(self, media: bytes, media_type: str, language: str | None) -> str:
        if not self._settings.speech_endpoint:
            raise RuntimeError("SPEECH_ENDPOINT is not configured")
        import httpx

        # A language hint is far more reliable than detection on short voice notes.
        locales = [self._LOCALES[language]] if language in self._LOCALES else list(self._LOCALES.values())
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self._settings.speech_endpoint.rstrip('/')}/speechtotext/transcriptions:transcribe",
                params={"api-version": self._SPEECH_API_VERSION},
                headers={"Authorization": f"Bearer {await self._token()}"},
                files={
                    "audio": ("voice-note", media, media_type),
                    "definition": (None, json.dumps({"locales": locales}), "application/json"),
                },
            )
        response.raise_for_status()
        phrases = response.json().get("combinedPhrases") or []
        return " ".join(phrase.get("text", "") for phrase in phrases).strip()


class AzureContentSafetyInspector:
    """Azure AI Content Safety: harm categories plus Prompt Shields (jailbreak detection).

    The 1.0 SDK has no Prompt Shields method, so that call goes through the SDK's
    authenticated ``send_request`` to the GA REST API.
    """

    _SHIELD_API_VERSION = "2024-09-01"
    _BLOCKING_SEVERITY = 4

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def assess(self, redacted_text: str) -> str | None:
        """Return None when the text is safe, otherwise a reason code."""
        if not self._settings.content_safety_endpoint:
            raise RuntimeError("CONTENT_SAFETY_ENDPOINT is not configured")

        from azure.ai.contentsafety.aio import ContentSafetyClient
        from azure.ai.contentsafety.models import AnalyzeTextOptions
        from azure.core.rest import HttpRequest
        from azure.identity.aio import DefaultAzureCredential

        async with DefaultAzureCredential() as credential:
            async with ContentSafetyClient(
                self._settings.content_safety_endpoint, credential
            ) as client:
                text_result = await client.analyze_text(AnalyzeTextOptions(text=redacted_text))
                shield_response = await client.send_request(
                    HttpRequest(
                        "POST",
                        "/text:shieldPrompt",
                        params={"api-version": self._SHIELD_API_VERSION},
                        json={"userPrompt": redacted_text, "documents": []},
                    )
                )
                shield_response.raise_for_status()
                shield: dict[str, Any] = shield_response.json()

        attack = (shield.get("userPromptAnalysis") or {}).get("attackDetected")
        if attack is None:
            raise RuntimeError("Prompt Shields result is missing attack status")
        if attack:
            return "prompt_attack_detected"

        if text_result is None:
            raise RuntimeError("Azure AI Content Safety returned an empty result")
        for category in text_result.categories_analysis or []:
            if (category.severity or 0) >= self._BLOCKING_SEVERITY:
                return f"harmful_content_{str(category.category).lower()}"[:64]
        return None


class FoundryMaintenanceClassifier:
    def __init__(
        self, settings: Settings, recorder: AgentRunRecorder | None = None
    ) -> None:
        self._settings = settings
        self._recorder = recorder
        prompt_bytes = (
            Path(__file__)
            .parents[1]
            .joinpath("prompts", "maintenance_triage_classifier.md")
            .read_bytes()
        )
        self._instructions = prompt_bytes.decode("utf-8")
        # Same short hash the eval runner reports, so runs and evals line up.
        self.prompt_sha = hashlib.sha256(prompt_bytes).hexdigest()[:12]
        self._cached_agent: Any = None

    async def classify(self, redacted_text: str) -> Classification:
        response = await self._agent().run(
            redacted_text, options={"response_format": Classification}
        )
        if isinstance(response.value, Classification):
            return response.value
        if isinstance(response.value, dict):
            return Classification.model_validate(response.value)
        raise RuntimeError("Foundry returned no valid structured classification")

    def _agent(self):
        """One agent and chat client per classifier, reused across requests.

        FoundryChatClient has no close(); building one per call leaked an HTTP
        connection pool per classification. Create the classifier once per process.
        """
        if self._cached_agent is None:
            if not self._settings.foundry_endpoint:
                raise RuntimeError("FOUNDRY_ENDPOINT is not configured")
            from agent_framework import Agent
            from agent_framework.foundry import FoundryChatClient
            from azure.identity import DefaultAzureCredential

            self._cached_agent = Agent(
                client=FoundryChatClient(
                    project_endpoint=self._settings.foundry_endpoint,
                    model=self._settings.foundry_model,
                    credential=DefaultAzureCredential(),
                ),
                name="Maintenance Triage Classifier",
                description="Classifies redacted maintenance requests without tools or side effects.",
                instructions=self._instructions,
                default_options={"store": False},
                middleware=(
                    [
                        AgentMonitoringMiddleware(
                            self._recorder,
                            model=self._settings.foundry_model,
                            prompt_sha=self.prompt_sha,
                        )
                    ]
                    if self._recorder
                    else None
                ),
            )
        return self._cached_agent
