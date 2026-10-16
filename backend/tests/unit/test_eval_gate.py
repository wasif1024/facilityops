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
