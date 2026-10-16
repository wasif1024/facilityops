---
name: facilityops-evaluation
description: "Use when building or changing FacilityOps AI test datasets, evaluators, model or workflow comparisons, CI quality gates, fairness checks, or safety evaluations. Covers the synthetic golden set and emergency-recall gate."
---

# FacilityOps Evaluation

## Scope

Evaluate the workflow against synthetic data. Keep evaluation inputs, outputs, and traces free of real tenant information and protected PII mappings.

## Procedure

1. Inspect the current evaluator SDK, project dependencies, and CI workflow. Consult current Microsoft guidance before using Foundry evaluator APIs.
2. Maintain a versioned synthetic golden set of approximately 100 maintenance requests, covering categories, priorities, languages, ambiguous wording, adversarial instructions, and emergency cases.
3. Measure category and priority classification accuracy, emergency recall, groundedness, and tool-call accuracy. Add metrics only with a clear definition and reproducible scoring rule.
4. Treat emergency recall as a hard CI gate. A missed emergency must fail the relevant check; do not average it away with non-emergency performance.
5. Add counterfactual fairness cases that vary names, language, or building while holding the underlying incident facts constant. Priority should remain consistent unless the facts relevant to risk change.
6. Verify that generated work-order actions respect approval rules and that the workflow does not claim unsupported facts from manuals or history.
7. Separate deterministic unit tests from model-based evaluation. Make model-based tests reproducible where possible, record model and prompt versions, and report expected variance.
8. Run the narrow relevant tests first, then the evaluation suite and CI gate. Report failures with case IDs and metric definitions, not sensitive request contents.

## Verification

- Dataset records are synthetic, versioned, and contain no PII mapping material.
- Emergency recall is computed from explicit labeled cases and fails CI below the approved threshold.
- Fairness pairs and evaluator versions are traceable, and results identify the model/prompt version used.