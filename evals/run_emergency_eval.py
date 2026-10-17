"""Emergency screen coverage by language and emergency type (offline, free, no Azure).

    python -m evals.run_emergency_eval [--dataset evals/datasets/emergency_holdout_v1.jsonl] [--report out.json]

The golden set's emergency cases are the ones the rules were written against, so its recall of 1.00 shows the
rules fit those cases. This runner measures how far they generalise: the held-out set has direct and indirect
descriptions of every emergency type in English, Spanish and French, plus near-misses that must NOT trigger
(a chirping smoke-alarm battery, a fire door that won't close). It reports recall per language and type,
whether the right type was named, and false alarms. It is a report, not a CI gate: misses are expected and are
the point of measuring.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from backend.app.domain.emergency import EmergencyType, detect_emergency

HOLDOUT = Path(__file__).parent / "datasets" / "emergency_holdout_v1.jsonl"
LANGUAGES = ("en", "es", "fr")


def evaluate(path: Path) -> dict:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    found: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    by_style: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    misses, wrong_type, false_alarms = [], [], []
    safe: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for case in cases:
        detected = detect_emergency(case["text"])
        if case["emergency"]:
            cell = found[(case["language"], case["emergency_type"])]
            cell[1] += 1
            for style in case.get("tags", []):
                by_style[style][1] += 1
            if detected is None:
                misses.append(case["id"])
                continue
            cell[0] += 1
            for style in case.get("tags", []):
                by_style[style][0] += 1
            if detected.value != case["emergency_type"]:
                wrong_type.append(f"{case['id']} ({detected.value})")
        else:
            safe[case["language"]][1] += 1
            if detected is not None:
                false_alarms.append(f"{case['id']} ({detected.value})")
            else:
                safe[case["language"]][0] += 1

    emergencies = sum(c[1] for c in found.values())
    return {
        "dataset": path.name,
        "recall": round(sum(c[0] for c in found.values()) / emergencies, 3) if emergencies else None,
        "recall_by_language": {
            lang: _ratio(sum(c[0] for (l, _), c in found.items() if l == lang), sum(c[1] for (l, _), c in found.items() if l == lang))
            for lang in LANGUAGES
        },
        "recall_by_type": {
            t.value: _ratio(sum(c[0] for (_, k), c in found.items() if k == t.value), sum(c[1] for (_, k), c in found.items() if k == t.value))
            for t in EmergencyType
        },
        "recall_by_style": {style: _ratio(*cell) for style, cell in sorted(by_style.items())},
        "grid": {f"{lang}/{kind}": f"{c[0]}/{c[1]}" for (lang, kind), c in sorted(found.items())},
        "near_misses_passed": {lang: f"{c[0]}/{c[1]}" for lang, c in sorted(safe.items())},
        "misses": misses,
        "wrong_type": wrong_type,
        "false_alarms": false_alarms,
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 3) if denominator else None


def render(report: dict) -> str:
    types = [t.value for t in EmergencyType]
    lines = [f"Emergency screen coverage: {report['dataset']}", "", f"recall {report['recall']}", ""]
    lines.append("| | " + " | ".join(types) + " | all |")
    lines.append("|---|" + "---|" * (len(types) + 1))
    for lang in LANGUAGES:
        row = [report["grid"].get(f"{lang}/{t}", "-") for t in types]
        lines.append(f"| {lang} | " + " | ".join(row) + f" | {report['recall_by_language'][lang]} |")
    lines += ["", f"by style: {report['recall_by_style']}", f"near-misses correctly ignored: {report['near_misses_passed']}"]
    lines += [f"missed: {', '.join(report['misses']) or 'none'}", f"named the wrong type: {', '.join(report['wrong_type']) or 'none'}"]
    lines.append(f"false alarms: {', '.join(report['false_alarms']) or 'none'}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", type=Path, default=HOLDOUT)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    report = evaluate(args.dataset)
    print(render(report))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
