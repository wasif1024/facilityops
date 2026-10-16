"""Deterministic emergency screen that runs before any redaction or model call.

Recall matters more than precision here: a false positive costs an on-call page,
a false negative leaves a gas leak in an AI queue. Ambiguous phrasing therefore
errs towards the emergency path. Every change is measured against the golden set
(``evals/run_eval.py``), and CI fails if emergency recall drops below the gate.
"""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum


class EmergencyType(StrEnum):
    GAS = "gas"
    CARBON_MONOXIDE = "carbon_monoxide"
    FIRE = "fire"
    ELECTRICAL = "electrical"
    FLOOD = "flood"


def _compile(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{pattern})" for pattern in patterns))


# Within one sentence: no terminal punctuation between the two terms.
_NEAR = r"[^.!?\n]{0,40}"

# A sounding alarm is an emergency; a chirping one with a low battery is not.
_ALARM_SOUNDING = _compile(
    rf"\b(?P<kind>fire|smoke|co|carbon monoxide) (?:alarm|detector)s?\b{_NEAR}"
    r"\b(?:going off|goes off|went off|gone off|sounding|ringing|activated|blaring)\b",
)

# Removed before the generic fire/smoke/flood terms are matched.
_BENIGN_PHRASES = _compile(
    r"\bfire (?:doors?|extinguishers?|drills?|exits?|escapes?|inspections?|safety|"
    r"marshals?|hydrants?|sprinklers?|code|alarms?(?: panel)?)\b",
    # CO alarms are deliberately not listed: any carbon monoxide mention escalates.
    r"\bsmoke (?:alarms?|detectors?)\b",
    r"\bno smoking\b",
    r"\bflood ?lights?\b",
)

_PATTERNS: tuple[tuple[EmergencyType, re.Pattern[str]], ...] = (
    (
        EmergencyType.CARBON_MONOXIDE,
        _compile(
            r"\bcarbon monoxide\b",
            r"\bco (?:alarm|detector)s?\b",
            r"\bmonoxido de carbono\b",
            r"\bmonoxyde de carbone\b",
        ),
    ),
    (
        EmergencyType.GAS,
        _compile(
            rf"\bgas\b{_NEAR}\b(?:leak\w*|smell\w*|odou?rs?|hiss\w*|stench)\b",
            rf"\b(?:leak\w*|smell\w*|odou?rs?|hiss\w*|stench|reek\w*)\b{_NEAR}\bgas\b",
            r"\brotten eggs?\b",
            r"\b(?:sulfur|sulphur) smell\b",
            r"\b(?:huele\w*|olor|fuga|escape)\b(?: \w+){0,2} (?:a|de) gas\b",
            r"\b(?:odeur|fuite|sent)\b(?: \w+){0,2} (?:de |le )?gaz\b",
        ),
    ),
    (
        EmergencyType.ELECTRICAL,
        _compile(
            r"\bspark(?:s|ing|ed)?\b",
            r"\b(?:live|exposed|bare) wires?\b",
            r"\b(?:electric(?:al)? shock|got a shock|shocked me)\b",
            r"\b(?:burning (?:smell|plastic|odou?r)|smell\w* (?:of|like) burning)\b",
            rf"\b(?:outlets?|sockets?|plugs?|panels?)\b{_NEAR}\b(?:melt\w*|scorch\w*)\b",
            r"\bchispas?\b",
            r"\bcables? (?:pelados|expuestos)\b",
            r"\betincelles?\b",
            r"\bfils? (?:denudes|a nu)\b",
        ),
    ),
    (
        EmergencyType.FIRE,
        _compile(
            r"\b(?:fires?|flames?|ablaze|smoke|smoking|smoky)\b",
            r"\b(?:incendio|fuego|humo|llamas)\b",
            r"\b(?:incendie|feu|fumee|flammes)\b",
        ),
    ),
    (
        EmergencyType.FLOOD,
        _compile(
            r"\bflood(?:s|ed|ing)?\b",
            r"\bunder (?:\w+ ){0,4}water\b",
            r"\bwater (?:is )?(?:pouring|gushing|rushing|spraying|everywhere|"
            r"coming (?:in|through|down))\b",
            rf"\bwater\b{_NEAR}\bthrough the ceiling\b",
            r"\b(?:burst|broken|ruptured) (?:water )?(?:pipe|main|line)s?\b",
            r"\b(?:pipes?|water (?:mains?|lines?)) (?:has |have |just )?"
            r"(?:burst|broke|ruptured|split)\b",
            rf"\bsewage\b{_NEAR}\b(?:backing up|backed up|overflow\w*|everywhere)\b",
            r"\binundad\w*\b|\binundacion\b",
            r"\b(?:rompio|reviento) (?:una |la )?tuberia\b|\btuberia (?:rota|reventada)\b",
            r"\bagua por todas partes\b",
            r"\binond\w*\b",
            r"\b(?:canalisation|tuyau)\w* (?:a )?eclate\w*\b",
            r"\beau (?:coule )?partout\b",
        ),
    ),
)


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[\s’']+", " ", stripped.casefold())


def detect_emergency(text: str) -> EmergencyType | None:
    """Return the first emergency signal found in raw request text, if any."""
    normalized = _normalize(text)

    alarm = _ALARM_SOUNDING.search(normalized)
    if alarm is not None:
        kind = alarm.group("kind")
        return (
            EmergencyType.CARBON_MONOXIDE
            if kind in {"co", "carbon monoxide"}
            else EmergencyType.FIRE
        )

    screened = _BENIGN_PHRASES.sub(" ", normalized)
    for emergency_type, pattern in _PATTERNS:
        if pattern.search(screened):
            return emergency_type
    return None
