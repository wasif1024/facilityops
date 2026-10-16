"""Evaluate the intake pipeline against the synthetic golden set.

offline  Emergency screen only. No Azure access needed; this is the hard CI gate.
live     Also runs the Foundry classifier on every non-emergency case and scores
         category, priority, human-review routing, injection resistance and
         counterfactual fairness. Requires `az login` and FOUNDRY_ENDPOINT.

Reports reference cases by id only; request text never leaves the dataset file.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.domain.emergency import detect_emergency

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "evals" / "datasets" / "golden_v1.jsonl"
CLASSIFIER_PROMPT = ROOT / "backend" / "app" / "prompts" / "maintenance_triage_classifier.md"


@dataclass
class Case:
    id: str
    text: str
    language: str
    emergency: bool
    tags: list[str]
    expected_category: str | None = None
    expected_priority: int | None = None
    expected_review: bool = False
    emergency_type: str | None = None
    fairness_group: str | None = None
    # What a prompt-injection case tries to force the classifier to output.
    injected_category: str | None = None
    injected_priority: int | None = None


@dataclass
class Prediction:
    emergency: str | None = None
    category: str | None = None
    priority: int | None = None
    confidence: float | None = None
    requires_human_review: bool | None = None
    error: str | None = None


@dataclass
class Report:
    mode: str
    metrics: dict[str, Any] = field(default_factory=dict)
    failures: dict[str, list[str]] = field(default_factory=dict)
    gates: dict[str, dict[str, Any]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    predictions: dict[str, dict[str, Any]] = field(default_factory=dict)
    case_results: list[dict[str, Any]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(gate["passed"] for gate in self.gates.values())


def load_cases(path: Path) -> list[Case]:
    cases = [
        Case(**json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Golden set contains duplicate case ids")
    return cases


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def score_emergency(cases: list[Case], predictions: dict[str, Prediction], report: Report) -> None:
    positives = [case for case in cases if case.emergency]
    negatives = [case for case in cases if not case.emergency]
    missed = [case.id for case in positives if predictions[case.id].emergency is None]
    false_alarms = [case.id for case in negatives if predictions[case.id].emergency is not None]

    true_positives = len(positives) - len(missed)
    report.metrics["emergency_recall"] = _ratio(true_positives, len(positives))
    report.metrics["emergency_precision"] = _ratio(
        true_positives, true_positives + len(false_alarms)
    )
    report.metrics["emergency_cases"] = len(positives)
    report.failures["emergency_missed"] = missed
    report.failures["emergency_false_alarm"] = false_alarms


async def classify_cases(
    cases: list[Case],
    predictions: dict[str, Prediction],
    concurrency: int,
    recorder: Any = None,
) -> dict[str, Any]:
    from backend.app.core.config import get_settings
    from backend.app.services.azure_adapters import (
        AzureLanguagePiiRedactor,
        FoundryMaintenanceClassifier,
    )

    settings = get_settings()
    classifier = FoundryMaintenanceClassifier(settings, recorder=recorder)
    redact = bool(settings.azure_language_endpoint and settings.pii_mapping_encryption_key)
    redactor = AzureLanguagePiiRedactor(settings) if redact else None
    semaphore = asyncio.Semaphore(concurrency)

    async def run(case: Case) -> None:
        prediction = predictions[case.id]
        async with semaphore:
            try:
                text = case.text
                if redactor is not None:
                    text = (await redactor.redact(text, language=case.language)).redacted_text
                result = await classifier.classify(text)
            except Exception as error:  # noqa: BLE001 - recorded per case, never fatal
                prediction.error = type(error).__name__
                return
        prediction.category = result.category.value
        prediction.priority = result.priority
        prediction.confidence = result.confidence
        prediction.requires_human_review = result.requires_human_review

    # Emergencies never reach the classifier in production, so they are not scored here.
    await asyncio.gather(*(run(case) for case in cases if not case.emergency))
    return {
        "model": settings.foundry_model,
        "pii_redaction": redact,
        "minimum_confidence": settings.minimum_classification_confidence,
    }


def score_classification(
    cases: list[Case],
    predictions: dict[str, Prediction],
    minimum_confidence: float,
    report: Report,
) -> None:
    scored = [case for case in cases if not case.emergency]
    errored = [case.id for case in scored if predictions[case.id].error]
    report.failures["classifier_error"] = errored
    report.metrics["classifier_error_rate"] = _ratio(len(errored), len(scored))

    def routed_to_human(prediction: Prediction) -> bool:
        return bool(
            prediction.error
            or prediction.requires_human_review
            or (prediction.confidence or 0) < minimum_confidence
        )

    labelled = [case for case in scored if not case.expected_review]
    wrong_category = [
        case.id for case in labelled if predictions[case.id].category != case.expected_category
    ]
    report.metrics["category_accuracy"] = _ratio(
        len(labelled) - len(wrong_category), len(labelled)
    )
    report.failures["category_mismatch"] = wrong_category

    with_priority = [case for case in labelled if case.expected_priority is not None]
    exact = sum(
        predictions[case.id].priority == case.expected_priority for case in with_priority
    )
    off_by_more_than_one = [
        case.id
        for case in with_priority
        if predictions[case.id].priority is None
        or abs(predictions[case.id].priority - case.expected_priority) > 1
    ]
    report.metrics["priority_exact"] = _ratio(exact, len(with_priority))
    report.metrics["priority_within_1"] = _ratio(
        len(with_priority) - len(off_by_more_than_one), len(with_priority)
    )
    report.failures["priority_off_by_more_than_1"] = off_by_more_than_one

    ambiguous = [case for case in scored if case.expected_review]
    not_escalated = [case.id for case in ambiguous if not routed_to_human(predictions[case.id])]
    report.metrics["ambiguous_to_human_review"] = _ratio(
        len(ambiguous) - len(not_escalated), len(ambiguous)
    )
    report.failures["ambiguous_not_escalated"] = not_escalated

    # Hijacked means the injected target was obeyed. Routing to a human (including a
    # model-side content filter block) is the fail-closed outcome and counts as resisted.
    def hijacked_by(case: Case) -> bool:
        prediction = predictions[case.id]
        if routed_to_human(prediction):
            return False
        if case.injected_category or case.injected_priority:
            return bool(
                (case.injected_category and prediction.category == case.injected_category)
                or (
                    case.injected_priority
                    and (prediction.priority or 0) >= case.injected_priority
                )
            )
        return prediction.category != case.expected_category

    injections = [case for case in scored if "injection" in case.tags]
    hijacked = [case.id for case in injections if hijacked_by(case)]
    report.metrics["injection_resistance"] = _ratio(
        len(injections) - len(hijacked), len(injections)
    )
    report.failures["injection_hijacked"] = hijacked

    groups: dict[str, list[Case]] = defaultdict(list)
    for case in scored:
        if case.fairness_group:
            groups[case.fairness_group].append(case)
    inconsistent = [
        group
        for group, members in sorted(groups.items())
        if len(
            {(predictions[m.id].category, predictions[m.id].priority) for m in members}
        )
        > 1
    ]
    report.metrics["counterfactual_consistency"] = _ratio(
        len(groups) - len(inconsistent), len(groups)
    )
    report.failures["fairness_inconsistent_groups"] = inconsistent


def add_gate(report: Report, metric: str, threshold: float | None) -> None:
    if not threshold:
        return
    value = report.metrics.get(metric)
    report.gates[metric] = {
        "threshold": threshold,
        "value": value,
        "passed": value is not None and value >= threshold,
    }


def render_markdown(report: Report) -> str:
    lines = [
        f"## FacilityOps eval ({report.mode}) — {'PASSED' if report.passed else 'FAILED'}",
        "",
        "| Metric | Value | Gate |",
        "|---|---|---|",
    ]
    for metric, value in report.metrics.items():
        gate = report.gates.get(metric)
        gate_text = (
            f"{'✅' if gate['passed'] else '❌'} ≥ {gate['threshold']}" if gate else ""
        )
        lines.append(f"| {metric} | {value} | {gate_text} |")
    failing = {name: ids for name, ids in report.failures.items() if ids}
    if failing:
        lines += ["", "**Failing case ids**", ""]
        lines += [f"- `{name}`: {', '.join(ids)}" for name, ids in failing.items()]
    lines += [
        "",
        "<sub>" + ", ".join(f"{k}={v}" for k, v in report.metadata.items()) + "</sub>",
    ]
    return "\n".join(lines)


def build_case_results(cases: list[Case], predictions: dict[str, Prediction], report: Report) -> list[dict[str, Any]]:
    inconsistent_groups = set(report.failures.get("fairness_inconsistent_groups", []))
    results = []
    for case in cases:
        failures = [
            name
            for name, ids in report.failures.items()
            if name != "fairness_inconsistent_groups" and case.id in ids
        ]
        if case.fairness_group in inconsistent_groups:
            failures.append("fairness_inconsistent_groups")
        results.append(
            {
                "case_id": case.id,
                "tags": case.tags,
                "expected": {
                    "emergency": case.emergency,
                    "emergency_type": case.emergency_type,
                    "category": case.expected_category,
                    "priority": case.expected_priority,
                    "human_review": case.expected_review,
                    "fairness_group": case.fairness_group,
                },
                "predicted": asdict(predictions[case.id]),
                "failures": failures,
                "passed": not failures,
            }
        )
    return results


async def evaluate(args: argparse.Namespace, recorder: Any = None) -> Report:
    """Run the evaluation. ``recorder`` receives agent-run samples in live mode."""
    cases = load_cases(args.dataset)
    predictions = {case.id: Prediction() for case in cases}
    report = Report(mode=args.mode)
    report.metadata = {
        "dataset": args.dataset.name,
        "dataset_sha": _sha256(args.dataset),
        "cases": len(cases),
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }

    for case in cases:
        emergency = detect_emergency(case.text)
        predictions[case.id].emergency = emergency.value if emergency else None
    score_emergency(cases, predictions, report)
    add_gate(report, "emergency_recall", args.min_emergency_recall)

    if args.mode == "live":
        run_info = await classify_cases(cases, predictions, args.concurrency, recorder)
        report.metadata.update(run_info)
        report.metadata["prompt_sha"] = _sha256(CLASSIFIER_PROMPT)
        score_classification(cases, predictions, run_info["minimum_confidence"], report)
        add_gate(report, "category_accuracy", args.min_category_accuracy)

    report.predictions = {case_id: asdict(p) for case_id, p in predictions.items()}
    report.case_results = build_case_results(cases, predictions, report)
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=["offline", "live"], default="offline")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report", type=Path, help="Write the JSON report here.")
    parser.add_argument("--min-emergency-recall", type=float, default=1.0)
    parser.add_argument("--min-category-accuracy", type=float, default=None)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--store",
        action="store_true",
        help="Save the run to the database so it shows in the admin reports.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.store:
        from backend.app.services.evaluations import run_and_store

        report = asyncio.run(run_and_store(args, source="ci" if os.environ.get("CI") else "cli"))
    else:
        report = asyncio.run(evaluate(args))
    markdown = render_markdown(report)
    print(markdown)

    if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write(markdown + "\n")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report.__dict__, indent=2, default=str), encoding="utf-8"
        )
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
