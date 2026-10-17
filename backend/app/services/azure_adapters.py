from __future__ import annotations

import hashlib
import json
import re
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

from backend.app.core.config import Settings
from backend.app.domain.decision_trail import note
from backend.app.domain.intake import (
    MAX_DOCUMENT_PAGES,
    Classification,
    PiiRedaction,
    RequestSource,
    Translation,
)
from backend.app.services.agent_monitoring import (
    AgentMonitoringMiddleware,
    AgentRunRecorder,
)


class AzureLanguagePiiRedactor:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def redact(
        self, text: str, language: str = "en", *, attachment: str = ""
    ) -> PiiRedaction:
        if not self._settings.azure_language_endpoint:
            raise RuntimeError("AZURE_LANGUAGE_ENDPOINT is not configured")
        if not self._settings.pii_mapping_encryption_key:
            raise RuntimeError("PII_MAPPING_ENCRYPTION_KEY is not configured")

        from azure.ai.textanalytics.aio import TextAnalyticsClient
        from azure.identity.aio import DefaultAzureCredential

        # The tenant's words and the attachment text are separate documents in one call, so
        # each can be checked on its own later, under one placeholder numbering.
        parts = [part for part in (text, attachment) if part]
        if not parts:
            raise ValueError("Nothing to redact")
        async with DefaultAzureCredential() as credential:
            async with TextAnalyticsClient(
                endpoint=self._settings.azure_language_endpoint,
                credential=credential,
            ) as client:
                results = await client.recognize_pii_entities(
                    parts,
                    language=language,
                    disable_service_logs=True,
                )

        if len(results) != len(parts) or any(getattr(r, "is_error", False) for r in results):
            raise RuntimeError("Azure AI Language could not redact the request")

        entities = [entity for result in results for entity in result.entities]
        note(
            service="Azure AI Language",
            version="PII detection",
            entities=len(entities),
            # Categories only (for example "Person", "PhoneNumber"); never the values.
            categories=sorted({str(entity.category) for entity in entities}),
            attachment_redacted=bool(attachment),
        )
        mapping: dict[str, str] = {}
        redacted_parts = [
            _replace_entities(part, result.entities, mapping)
            for part, result in zip(parts, results, strict=True)
        ]
        redacted_user = redacted_parts[0] if text else ""
        redacted_attachment = redacted_parts[-1] if attachment else ""

        fernet = Fernet(self._settings.pii_mapping_encryption_key.encode("ascii"))
        encrypted_mapping = fernet.encrypt(
            json.dumps(mapping, ensure_ascii=True).encode("utf-8")
        )
        return PiiRedaction(
            redacted_text="\n".join(redacted_parts),
            encrypted_mapping=encrypted_mapping,
            redacted_user_text=redacted_user,
            redacted_attachment=redacted_attachment,
        )


def _replace_entities(text: str, entities: Any, mapping: dict[str, str]) -> str:
    """Replace each entity with the next [PII_nnnn] placeholder, numbering on from ``mapping``."""
    ordered = sorted(entities, key=lambda entity: entity.offset)
    first = len(mapping) + 1
    redacted = text
    last_start = len(text)
    for index, entity in reversed(list(enumerate(ordered, start=first))):
        start = entity.offset
        end = start + entity.length
        if end > last_start or start < 0 or end > len(text):
            raise RuntimeError("Azure AI Language returned overlapping or invalid PII offsets")
        placeholder = f"[PII_{index:04d}]"
        redacted = redacted[:start] + placeholder + redacted[end:]
        mapping[placeholder] = entity.text
        last_start = start
    return redacted


_COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"


class AzureMediaTranscriber:
    """Turns photos, documents and voice notes into text with Azure AI services.

    Photos: Vision Image Analysis 4.0 caption (gender-neutral) plus OCR. Documents:
    Document Intelligence ``prebuilt-read`` on the first pages. Voice: Speech fast
    transcription. Media goes only to these services, never to a language model, and
    is not stored; the resulting text then goes through PII redaction like typed text.
    """

    _VISION_API_VERSION = "2024-02-01"
    _SPEECH_API_VERSION = "2024-11-15"
    _DOCUMENT_API_VERSION = "2024-11-30"
    _DOCUMENT_TIMEOUT_SECONDS = 60
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
        if source is RequestSource.DOCUMENT:
            return await self._read_document(media, media_type, language)
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
        note(
            service="Azure AI Vision",
            version=f"Image Analysis {self._VISION_API_VERSION}",
            caption_confidence=round(caption.get("confidence", 0), 3),
            ocr_lines=sum(len(b.get("lines", [])) for b in (result.get("readResult") or {}).get("blocks", [])),
        )
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

    async def _read_document(self, media: bytes, media_type: str, language: str | None) -> str:
        """Document Intelligence read model: submit, then poll the operation until it ends."""
        if not self._settings.document_intelligence_endpoint:
            raise RuntimeError("DOCUMENT_INTELLIGENCE_ENDPOINT is not configured")
        import asyncio
        import time

        import httpx

        params = {"api-version": self._DOCUMENT_API_VERSION, "pages": f"1-{MAX_DOCUMENT_PAGES}"}
        if language:
            params["locale"] = language
        headers = {"Authorization": f"Bearer {await self._token()}"}
        deadline = time.monotonic() + self._DOCUMENT_TIMEOUT_SECONDS
        async with httpx.AsyncClient(timeout=30) as client:
            submitted = await client.post(
                f"{self._settings.document_intelligence_endpoint.rstrip('/')}"
                "/documentintelligence/documentModels/prebuilt-read:analyze",
                params=params,
                headers={**headers, "Content-Type": media_type},
                content=media,
            )
            submitted.raise_for_status()
            operation = submitted.headers["Operation-Location"]
            while True:
                await asyncio.sleep(_retry_after(submitted, default=1.0))
                submitted = await client.get(operation, headers=headers)
                submitted.raise_for_status()
                result = submitted.json()
                if result.get("status") == "succeeded":
                    analysis = result.get("analyzeResult") or {}
                    note(
                        service="Azure AI Document Intelligence",
                        version=f"prebuilt-read {self._DOCUMENT_API_VERSION}",
                        pages_read=len(analysis.get("pages", [])),
                        page_limit=MAX_DOCUMENT_PAGES,
                    )
                    return analysis.get("content", "")
                if result.get("status") == "failed":
                    raise RuntimeError("Document Intelligence could not read the document")
                if time.monotonic() > deadline:
                    raise TimeoutError("Document Intelligence did not finish in time")

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
        note(
            service="Azure AI Speech",
            version=f"fast transcription {self._SPEECH_API_VERSION}",
            locales=locales,
            duration_ms=(response.json().get("durationMilliseconds")),
        )
        return " ".join(phrase.get("text", "") for phrase in phrases).strip()


class AzureTranslator:
    """Azure AI Translator (Text v3.0) for staff translations, with Entra ID.

    Called with redacted text only. [PII_nnnn] placeholders pass through unchanged, so the
    reveal mapping applies to the translation too; a translation that lost a placeholder is
    discarded rather than shown with a detail missing.
    """

    _API_VERSION = "3.0"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def translate(self, text: str, *, source_language: str | None) -> Translation | None:
        if not self._settings.translator_endpoint:
            raise RuntimeError("TRANSLATOR_ENDPOINT is not configured")
        import httpx
        from azure.identity.aio import DefaultAzureCredential

        target = self._settings.staff_language
        params = {"api-version": self._API_VERSION, "to": target}
        if source_language:
            params["from"] = source_language
        async with DefaultAzureCredential() as credential:
            token = (await credential.get_token(_COGNITIVE_SERVICES_SCOPE)).token
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{self._settings.translator_endpoint.rstrip('/')}/translator/text/v3.0/translate",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                json=[{"text": text}],
            )
        response.raise_for_status()
        note(service="Azure AI Translator", version=f"Text {self._API_VERSION}", target_language=target)
        return _parse_translation(response.json(), text, source_language, target)


def _parse_translation(
    body: Any, original: str, source_language: str | None, target: str
) -> Translation | None:
    item = body[0]
    language = (source_language or (item.get("detectedLanguage") or {}).get("language") or "").lower()
    if not language or language == target:
        return None
    translated = item["translations"][0]["text"]
    if sorted(_PLACEHOLDERS.findall(translated)) != sorted(_PLACEHOLDERS.findall(original)):
        raise RuntimeError("Translation changed the personal-data placeholders")
    return Translation(text=translated, source_language=language)


_PLACEHOLDERS = re.compile(r"\[PII_\d{4}\]")


class AzureContentSafetyInspector:
    """Azure AI Content Safety: harm categories for text and images, plus Prompt Shields.

    Prompt Shields checks the tenant's words as a user prompt (jailbreaks) and text taken
    from photos and documents as documents (instructions hidden in an attachment). The 1.0
    SDK has no Prompt Shields method, so that call goes through the SDK's authenticated
    ``send_request`` to the GA REST API.
    """

    _SHIELD_API_VERSION = "2024-09-01"
    _BLOCKING_SEVERITY = 4
    # Image analysis limits: 4 MB and 7,200 pixels per side. Larger images are downscaled
    # for the check only; supported formats are sent as they are.
    _IMAGE_MAX_BYTES = 4 * 1024 * 1024
    _IMAGE_MAX_SIDE = 7_200
    _IMAGE_FORMATS = {"JPEG", "PNG", "GIF", "BMP", "TIFF", "WEBP"}

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def assess(self, redacted_text: str, *, documents: Sequence[str] = ()) -> str | None:
        """Return None when the text is safe, otherwise a reason code."""
        from azure.ai.contentsafety.models import AnalyzeTextOptions
        from azure.core.rest import HttpRequest

        documents = [document for document in documents if document]
        async with self._client() as client:
            text_result = await client.analyze_text(
                AnalyzeTextOptions(text="\n".join([redacted_text, *documents]).strip())
            )
            shield_response = await client.send_request(
                HttpRequest(
                    "POST",
                    "/text:shieldPrompt",
                    params={"api-version": self._SHIELD_API_VERSION},
                    json={"userPrompt": redacted_text, "documents": documents},
                )
            )
            shield_response.raise_for_status()
            shield: dict[str, Any] = shield_response.json()

        note(
            service="Azure AI Content Safety",
            version=f"Prompt Shields {self._SHIELD_API_VERSION}",
            harm_severities={str(c.category): c.severity or 0 for c in (text_result.categories_analysis or [])}
            if text_result
            else None,
            user_prompt_attack=(shield.get("userPromptAnalysis") or {}).get("attackDetected"),
            document_attacks=[bool(d.get("attackDetected")) for d in shield.get("documentsAnalysis") or []],
            blocking_severity=self._BLOCKING_SEVERITY,
        )
        if redacted_text:
            attack = (shield.get("userPromptAnalysis") or {}).get("attackDetected")
            if attack is None:
                raise RuntimeError("Prompt Shields result is missing attack status")
            if attack:
                return "prompt_attack_detected"
        document_results = shield.get("documentsAnalysis") or []
        if len(document_results) != len(documents):
            raise RuntimeError("Prompt Shields result is missing document analysis")
        if any(result.get("attackDetected") for result in document_results):
            return "document_attack_detected"

        if text_result is None:
            raise RuntimeError("Azure AI Content Safety returned an empty result")
        for category in text_result.categories_analysis or []:
            if (category.severity or 0) >= self._BLOCKING_SEVERITY:
                return f"harmful_content_{str(category.category).lower()}"[:64]
        return None

    async def assess_image(self, image: bytes) -> str | None:
        """Return None when the image is safe, otherwise "harmful_image_<category>"."""
        from azure.ai.contentsafety.models import AnalyzeImageOptions, ImageData

        async with self._client() as client:
            result = await client.analyze_image(
                AnalyzeImageOptions(image=ImageData(content=self._fit_image(image)))
            )
        if result is None or not result.categories_analysis:
            raise RuntimeError("Azure AI Content Safety returned an empty image result")
        note(
            service="Azure AI Content Safety",
            version="image analysis",
            image_severities={str(c.category): c.severity or 0 for c in result.categories_analysis},
            blocking_severity=self._BLOCKING_SEVERITY,
        )
        for category in result.categories_analysis:
            if (category.severity or 0) >= self._BLOCKING_SEVERITY:
                return f"harmful_image_{str(category.category).lower()}"[:64]
        return None

    async def screen_documents(self, texts: Sequence[str]) -> list[bool]:
        """Prompt Shields document check for retrieved passages: True where an attack is found."""
        from azure.core.rest import HttpRequest

        flags: list[bool] = []
        # The service checks several documents per call; keep batches small.
        for start in range(0, len(texts), 5):
            batch = [t[:5_000] for t in texts[start : start + 5]]
            async with self._client() as client:
                response = await client.send_request(
                    HttpRequest(
                        "POST",
                        "/text:shieldPrompt",
                        params={"api-version": self._SHIELD_API_VERSION},
                        json={"userPrompt": "", "documents": batch},
                    )
                )
                response.raise_for_status()
            results = response.json().get("documentsAnalysis") or []
            if len(results) != len(batch):
                raise RuntimeError("Prompt Shields result is missing document analysis")
            flags.extend(bool(r.get("attackDetected")) for r in results)
        return flags

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[Any]:
        if not self._settings.content_safety_endpoint:
            raise RuntimeError("CONTENT_SAFETY_ENDPOINT is not configured")
        from azure.ai.contentsafety.aio import ContentSafetyClient
        from azure.identity.aio import DefaultAzureCredential

        async with DefaultAzureCredential() as credential:
            async with ContentSafetyClient(
                self._settings.content_safety_endpoint, credential
            ) as client:
                yield client

    @classmethod
    def _fit_image(cls, image: bytes) -> bytes:
        """The image as the service accepts it: within its size limits, first frame only."""
        import io

        from PIL import Image

        with Image.open(io.BytesIO(image)) as picture:
            fits = (
                len(image) <= cls._IMAGE_MAX_BYTES
                and max(picture.size) <= cls._IMAGE_MAX_SIDE
                and picture.format in cls._IMAGE_FORMATS
            )
            if fits:
                return image
            picture.seek(0)
            frame = picture.convert("RGB")
        frame.thumbnail((2048, 2048))
        output = io.BytesIO()
        frame.save(output, "JPEG", quality=85)
        return output.getvalue()


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
        note(service="Microsoft Foundry", version=self._settings.foundry_model, prompt_sha=self.prompt_sha)
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
                            instructions=self._instructions,
                            capture_content=self._settings.capture_model_content,
                        )
                    ]
                    if self._recorder
                    else None
                ),
            )
        return self._cached_agent


def _retry_after(response: Any, default: float) -> float:
    """Seconds to wait before polling again, from Retry-After, bounded to 0.5-5 s."""
    try:
        seconds = float(response.headers.get("Retry-After", default))
    except ValueError:
        seconds = default
    return min(max(seconds, 0.5), 5.0)
