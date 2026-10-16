# FacilityOps AI

Portfolio project: an AI-assisted Facility Management system that showcases agent orchestration,
enterprise AI practices, PII handling, evaluation and Responsible AI on Microsoft Foundry / Azure.
Intended for the owner's public GitHub profile, so the README, diagrams and demo quality matter.

## Status
Not started. Next step: Phase 1 scaffold. Before writing agent code, check the current
Microsoft Agent Framework and Foundry SDK APIs (they change quickly); don't rely on memory.

## Azure environment
- Use the existing Azure subscription and Foundry project; don't create new ones unless asked.
- Endpoint lives in `.env` (`FOUNDRY_ENDPOINT`), which is gitignored. Never commit keys or endpoints.
- Auth: Entra ID via `DefaultAzureCredential` (the user is signed in with `az login`). No API keys.

## Architecture
**Workflow (deterministic intake pipeline)**, run for every request (text, photo, voice):
1. Content Safety + Prompt Shields
2. PII detection/redaction (Azure AI Language)
3. Classify category (HVAC, plumbing, electrical, security...) and priority
4. Emergency bypass: gas/fire/flood go straight to a human on-call, not the AI
5. Create work order
6. Human approval when cost or safety risk is above a threshold

**Magentic team (dynamic) for complex incidents**: the manager agent plans and re-plans across:
- Diagnostician: RAG over equipment manuals (Azure AI Search)
- Asset historian: function tools over maintenance history (SQL/Cosmos)
- Safety & compliance: lockout/tagout rules, regulatory checks
- Procurement: parts, vendor SLAs and contracts
- Scheduler: technician availability and skills
- Communicator: multilingual tenant notices
Cap the rounds and tokens per run. Use smaller models for specialists to control cost.

## Cross-cutting requirements
- **Enterprise:** managed identity, RBAC roles (tenant / technician / facility manager),
  Key Vault, an audit trail of agent decisions, OpenTelemetry tracing to App Insights.
- **PII:** not all-or-nothing. Store redacted text plus a protected mapping that only authorized roles
  can reveal (a technician needs the unit number; logs, analytics and eval data never hold PII).
- **Evaluation:** synthetic golden set (~100 requests). Metrics: classification accuracy,
  emergency recall (hard CI gate), groundedness, tool-call accuracy. Foundry evaluators plus
  custom metrics, run in GitHub Actions on PRs.
- **Responsible AI:** prompt-injection test cases, counterfactual fairness evals (same request with a
  different name, language or building should get the same priority), AI Red Teaming agent (PyRIT)
  report, human oversight for safety-critical and costly actions, `RESPONSIBLE_AI.md` transparency note.
- **Synthetic data only:** fake buildings, tenants and manuals. Nothing real in the public repo.

## Phases
1. MVP: intake workflow, PII, classification, eval set and CI gate
2. Magentic incident team, RAG over sample manuals, approvals
3. Web UI (tenant + manager views), `azd`/Bicep infra, red-team report, architecture diagram, demo GIF

## Conventions
- Python
- Commit only when the user asks
