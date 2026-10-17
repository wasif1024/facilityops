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
_ALARM_SOUNDING = (
    _compile(
        rf"\b(?P<kind>fire|smoke|co|carbon monoxide) (?:alarm|detector)s?\b{_NEAR}"
        r"\b(?:going off|goes off|went off|gone off|sounding|ringing|activated|blaring)\b",
    ),
    _compile(
        rf"\b(?:alarma|detector)(?:es)? de (?P<kind>humo|incendios?|co|monoxido(?: de carbono)?)\b{_NEAR}"
        r"\b(?:sonando|suena|sono|pitando|pita|no para|activad\w*)\b",
    ),
    _compile(
        rf"\b(?:alarme|detecteur)s? (?:de |d )?(?P<kind>fumee|incendie|co|monoxyde(?: de carbone)?)\b{_NEAR}"
        r"\b(?:sonne|retentit|bipe|declench\w*)\b",
    ),
)
_CO_KINDS = ("co", "carbon monoxide", "monoxido", "monoxyde")
# "The battery is low" makes a beeping alarm a maintenance job, in any of the three languages.
_LOW_BATTERY = _compile(r"\b(?:batter(?:y|ies)|pilas?|bateria\w*|piles?|batterie\w*)\b")

# Carbon monoxide shows itself through people, not smell: symptoms together with something that burns fuel,
# or a yellow or orange flame on a gas appliance. Matched anywhere in the request, not just one sentence.
_CO_SYMPTOMS = _compile(
    r"\bheadaches?\b", r"\bdizz\w*", r"\bnause\w*", r"\bvomit\w*", r"\bfaint\w*", r"\bpassed out\b",
    r"\bdrows\w*", r"\bsleepy\b", r"\bfeel(?:s|ing)? (?:sick|ill|unwell)\b",
    r"\bdolor(?:es)? de cabeza\b", r"\bmareo\w*", r"\bdesmay\w*", r"\bsomnolen\w*",
    r"\bmal a la tete\b", r"\bmaux de tete\b", r"\bvertige\w*", r"\bevanoui\w*", r"\bnausee\w*",
)
_FUEL_BURNING = _compile(
    r"\bboilers?\b", r"\bheating\b", r"\bheaters?\b", r"\bfurnaces?\b", r"\bgas fires?\b", r"\bstoves?\b",
    r"\bfireplaces?\b", r"\bcaldera\w*", r"\bcalefaccion\b", r"\bcalentador\w*", r"\bestufa\w*",
    r"\bchaudiere\w*", r"\bchauffage\b", r"\bpoeles?\b", r"\bcheminee\w*",
)
_DISCOLOURED_FLAME = _compile(
    r"\b(?:orange|yellow) flames?\b", rf"\bflames?\b{_NEAR}\b(?:orange|yellow)\b",
    rf"\bllamas?\b{_NEAR}\b(?:amarill\w*|naranja)\b", rf"\bflammes?\b{_NEAR}\b(?:jaune\w*|orange\w*)\b",
)

# Removed before the generic fire/smoke/flood terms are matched.
_BENIGN_PHRASES = _compile(
    r"\bfire (?:doors?|extinguishers?|drills?|exits?|escapes?|inspections?|safety|"
    r"marshals?|hydrants?|sprinklers?|code|alarms?(?: panel)?)\b",
    # CO alarms are deliberately not listed: any carbon monoxide mention escalates.
    r"\bsmoke (?:alarms?|detectors?)\b",
    r"\bno smoking\b",
    r"\bflood ?lights?\b",
    r"\b(?:detector|alarma)(?:es)? de humo\b",
    r"\b(?:salidas?|simulacros?) de (?:emergencia|incendios?)\b",
    r"\b(?:detecteur|alarme)s? de fumee\b",
    r"\b(?:portes? )?coupe-? ?feux?\b",
    r"\bsorties? de secours\b",
    r"\bexercices? (?:d )?incendie\b",
)

_PATTERNS: tuple[tuple[EmergencyType, re.Pattern[str]], ...] = (
    (
        EmergencyType.CARBON_MONOXIDE,
        _compile(
            r"\bcarbon monoxide\b",
            r"\bco (?:alarm|detector)s?\b",
            r"\bmonox\w*",  # monoxide, monoxido, monoxyde: any mention escalates
            r"\b(?:detector|alarma)(?:es)? de co\b",
            r"\b(?:alarme|detecteur)s? (?:de )?co\b",
            rf"\b(?:alarm|detector|beep|screen|display)\w*\b{_NEAR}\bco\b",
            rf"\bco\b{_NEAR}\b(?:alarm|detector|beep)\w*",
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
            rf"\b(?:gas|gaz)\b{_NEAR}\b(?:fuga|olor|huele|silbid|escape|fuite|odeur|sent|siffl)\w*",
            rf"\b(?:fuga|olor|huele|silbid|fuite|odeur|siffl)\w*\b{_NEAR}\b(?:gas|gaz)\b",
            r"\b(?:rotten|bad) eggs?\b",
            r"\beggs? (?:gone )?(?:bad|off|rotten)\b",
            r"\bhuevos? podridos?\b",
            r"\b(?:oeufs?|œufs?) pourris?\b",
            rf"\b(?:smell\w*|odou?r\w*|stench|reek\w*|hiss\w*)\b{_NEAR}\b(?:meter|cooker|hob|stove|oven|pilot light)s?\b",
            rf"\b(?:meter|cooker|hob|stove|oven|pilot light)s?\b{_NEAR}\b(?:smell\w*|odou?r\w*|stench|reek\w*|hiss\w*)\b",
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
            r"\bjolts?\b",
            r"\b(?:shocks? me|gives? me a shock|got shocked|electrocut\w*)\b",
            rf"\b(?:sockets?|outlets?|plugs?|switch\w*|fuse ?box\w*|breakers?|panels?|consumer units?)\b{_NEAR}"
            r"\b(?:black\w*|scorch\w*|melt\w*|hot|buzz\w*|smok\w*|burn\w*)\b",
            rf"\b(?:cables?|wires?|wiring)\b{_NEAR}\b(?:copper|exposed|bare|hanging|dangling|frayed)\b",
            r"\b(?:me dio|recibi|da|dio) (?:una )?(?:descarga|calambre)\b|\bdescarga electrica\b|\bcalambrazo\b",
            rf"\bcables?\b{_NEAR}\b(?:suelt|colgand|pelad|expuest|desnud)\w*",
            rf"\b(?:enchufes?|interruptor\w*|cuadro\w*|tablero\w*)\b{_NEAR}\b(?:quemad\w*|caliente|chispa\w*|humo|derriti\w*)\b",
            r"\bhuele a quemado\b",
            r"\b(?:pris|prend|recu) (?:une )?(?:decharge|chataigne|coup de jus)\b|\bdecharge electrique\b",
            rf"\b(?:cables?|fils?)\b{_NEAR}\b(?:denud|a nu|pend|expos)\w*",
            rf"\b(?:tableau\w*|prises?|interrupteurs?|disjoncteurs?)\b{_NEAR}\b(?:brul|chauff|chaud|etincel|fum|noirc|fond)\w*",
            r"\b(?:sent le|odeur de) brule\b",
        ),
    ),
    (
        EmergencyType.FIRE,
        _compile(
            r"\b(?:fires?|flames?|ablaze|smoke|smoking|smoky)\b",
            r"\b(?:incendio|fuego|humo|llamas)\b",
            r"\b(?:incendie|feu|fumee|flammes)\b",
            r"\b(?:burning|on fire)\b",
            rf"\bglow\w*\b{_NEAR}\b(?:red|orange|hot)\b|\b(?:orange|red) glow\b",
            r"\bquem\w*",
            r"\bbrul\w*",
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
            # Moving water, not a stain: "water is dripping from the light fitting", "won't stop", "ankle deep".
            r"\bwater\b[^.!?\n]{0,25}\b(?:dripping|pouring|running|coming|leaking|gushing|streaming)\b"
            rf"{_NEAR}\b(?:ceiling|light fitting|walls?|floor drain|through)\b",
            rf"\bwater\b{_NEAR}\b(?:won t stop|wont stop|not stopping|ankle|knee)\b",
            rf"\bceilings?\b{_NEAR}\b(?:bulg|sagg|collaps|caving)\w*",
            rf"\b(?:tank|cylinder|hose|water heater|pipe)s?\b{_NEAR}\b(?:split|burst|came off|come off|broke|ruptured)\b",
            rf"\bagua\b{_NEAR}\b(?:techo|sin parar|no para|entrando|entra|por todo|por todas|debajo de|suelo|piso)\b",
            rf"\b(?:revent|romp|rot[oa])\w*\b{_NEAR}\b(?:tuberia|calentador|termo|deposito|cano)\w*",
            rf"\b(?:tuberia|calentador|termo|deposito|cano)\w*\b{_NEAR}\b(?:revent|rot[oa]|romp)\w*",
            rf"\beau\b{_NEAR}\b(?:plafond|sans arret|rentre|entre|partout|sous la porte|ne s arrete pas)\b",
            rf"\b(?:eclat|lache|creve|cass)\w*\b{_NEAR}\b(?:tuyau|chauffe-eau|canalisation|ballon|cumulus)\w*",
            rf"\b(?:tuyau|chauffe-eau|canalisation|ballon|cumulus)\w*\b{_NEAR}\b(?:eclat|lache|creve|cass)\w*",
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

    for pattern in _ALARM_SOUNDING:
        alarm = pattern.search(normalized)
        if alarm is None:
            continue
        kind = alarm.group("kind")
        if kind.startswith(_CO_KINDS):
            return EmergencyType.CARBON_MONOXIDE
        if not _LOW_BATTERY.search(normalized):
            return EmergencyType.FIRE

    screened = _BENIGN_PHRASES.sub(" ", normalized)
    if _FUEL_BURNING.search(screened) and (_CO_SYMPTOMS.search(screened) or _DISCOLOURED_FLAME.search(screened)):
        return EmergencyType.CARBON_MONOXIDE
    for emergency_type, pattern in _PATTERNS:
        if pattern.search(screened):
            return emergency_type
    return None
