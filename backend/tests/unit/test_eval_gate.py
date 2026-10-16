import json

from evals.run_eval import DEFAULT_DATASET, load_cases, main


def _write_dataset(path, cases):
    path.write_text("\n".join(json.dumps(case) for case in cases), encoding="utf-8")
    return path


def test_golden_set_is_well_formed():
    cases = load_cases(DEFAULT_DATASET)

    assert len(cases) >= 100
    assert sum(case.emergency for case in cases) >= 20
    for case in cases:
        if case.emergency:
            assert case.emergency_type
        else:
            assert case.expected_category
            assert case.expected_review or case.expected_priority


def test_gate_fails_when_an_emergency_is_missed(tmp_path):
    dataset = _write_dataset(
        tmp_path / "cases.jsonl",
        [
            {
                "id": "X1",
                "text": "Something unusual is happening in the boiler room",
                "language": "en",
                "emergency": True,
                "emergency_type": "gas",
                "tags": ["emergency"],
            }
        ],
    )
    report_path = tmp_path / "report.json"

    exit_code = main(["--dataset", str(dataset), "--report", str(report_path)])

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert report["failures"]["emergency_missed"] == ["X1"]
    assert "boiler" not in report_path.read_text(encoding="utf-8")


def test_gate_passes_on_the_golden_set():
    assert main([]) == 0


def _sample(category="plumbing", priority=2, confidence=0.9, review=False):
    return {
        "category": category,
        "priority": priority,
        "confidence": confidence,
        "requires_human_review": review,
    }


def _case(case_id, group=None):
    from evals.run_eval import Case

    return Case(
        id=case_id,
        text="synthetic",
        language="en",
        emergency=False,
        tags=["fairness"] if group else [],
        expected_category="plumbing",
        expected_priority=2,
        fairness_group=group,
    )


def _score(samples_by_case, groups=None):
    from evals.run_eval import Prediction, Report, score_consistency

    groups = groups or {}
    cases = [_case(case_id, groups.get(case_id)) for case_id in samples_by_case]
    predictions = {
        case_id: Prediction(samples=samples) for case_id, samples in samples_by_case.items()
    }
    report = Report(mode="live")
    score_consistency(cases, predictions, 0.75, 4, report)
    return report


def test_label_flip_inside_one_route_is_not_a_route_flip():
    # Priority 2 vs 3: different label, same route (automatic work order).
    report = _score({"P1": [_sample(priority=2), _sample(priority=3), _sample(priority=2)]})

    assert report.metrics["classification_self_agreement"] == 0.0
    assert report.metrics["route_self_agreement"] == 1.0
    assert report.failures["unstable_route"] == []


def test_flips_across_decision_boundaries_are_route_flips():
    report = _score(
        {
            "APPROVAL": [_sample(priority=3), _sample(priority=4)],  # crosses approval threshold
            "REVIEW": [_sample(confidence=0.9), _sample(confidence=0.7)],  # crosses 0.75
            "STABLE": [_sample(), _sample()],
        }
    )

    assert report.failures["unstable_route"] == ["APPROVAL", "REVIEW"]
    assert report.metrics["route_self_agreement"] == round(1 / 3, 4)


def test_modal_fairness_ignores_one_unlucky_draw_but_catches_systematic_splits():
    noisy = _score(
        {
            "Fa": [_sample(priority=3), _sample(priority=3), _sample(priority=2)],
            "Fb": [_sample(priority=2), _sample(priority=3), _sample(priority=3)],
        },
        groups={"Fa": "F", "Fb": "F"},
    )
    biased = _score(
        {
            "Fa": [_sample(priority=4)] * 3,
            "Fb": [_sample(priority=2)] * 3,
        },
        groups={"Fa": "F", "Fb": "F"},
    )

    assert noisy.metrics["counterfactual_consistency_modal"] == 1.0
    assert biased.metrics["counterfactual_consistency_modal"] == 0.0
    assert biased.failures["fairness_route_inconsistent_groups_modal"] == ["F"]


def test_single_sample_runs_report_no_consistency_metrics():
    report = _score({"P1": [_sample()]})

    assert "route_self_agreement" not in report.metrics


def test_route_fairness_gate_needs_samples():
    from evals.run_eval import Report, add_gate

    report = Report(mode="live", metrics={"counterfactual_route_consistency_modal": 0.75})
    add_gate(report, "counterfactual_route_consistency_modal", 1.0)

    assert report.gates["counterfactual_route_consistency_modal"]["passed"] is False
    assert not report.passed
