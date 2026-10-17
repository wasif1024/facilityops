# FacilityOps AI

Portfolio project: an AI-assisted Facility Management system that showcases agent orchestration,
enterprise AI practices, PII handling, evaluation and Responsible AI on Microsoft Foundry / Azure.
Intended for the owner's public GitHub profile, so the README, diagrams and demo quality matter.

## Status
Phase 1 complete (2026-10-17; results in docs/specification.md §10). Done: Connect RPC API (protobuf in proto/, served from the FastAPI/ASGI host), deterministic coordinator, emergency screen,
PII redaction adapter, Foundry classifier, Postgres/Alembic, golden set + eval runner + CI gate,
user management + DB-backed RBAC (Entra authenticates, DB authorizes), security audit log,
agent-run monitoring middleware, stored eval runs, admin report API. See docs/ for spec and architecture.
photo/voice/document intake (Vision caption+OCR, Speech, Document Intelligence prebuilt-read), upload checks by
file bytes (backend/app/domain/media.py), image Content Safety, Prompt Shields with attachment text as documents,
review queue, work-order lifecycle, audited PII reveal,
OpenTelemetry to App Insights (no content). Next: Phase 2 (Magentic incident team).
Identity: residents = Entra External ID (provider `entra_external`, default role tenant), staff = workforce
Entra ID (no default role); backend/app/api/token_validation.py; setup in docs/identity-setup.md and
scripts/setup_entra_external.sh. Frontend talks gRPC-Web (createGrpcWebTransport).
Frontend: `frontend/` (React 19 + TS + Vite + Tailwind 4 + shadcn/ui, Node 24 via .nvmrc), spec in
docs/frontend-specification.md. UI shows actions by permission from GetMe; revealed PII only in component
state; after a .proto change run `npm run generate` in frontend/ too.
Run locally with `scripts/dev.sh` (both; `backend` / `frontend` alone; `--entra`); it wraps
`uvicorn backend.app.main:create_app --factory` (no module-level app). Or the whole
stack with `docker compose up --build -d` (Dockerfile = runtime deps only; Postgres on host port 5433;
optional Azure settings in gitignored `.env.docker`).
Before writing agent code, check the current Microsoft Agent Framework and Foundry SDK APIs
(they change quickly); don't rely on memory.

Migrations: `scripts/db.sh` (no args = create DB if needed + apply all), `up|down|status|new "msg"|check|reset|sql`
(wraps Alembic; user-facing docs show only db.sh commands; new files are named
`NNNN_slug.py`). Commands (run from repo root): `python -m pytest -q backend/tests/unit`,
`python -m pytest -q backend/tests/integration` (needs local PostgreSQL),
`python -m evals.run_eval [--mode live] [--store]`, `python -m evals.run_media_eval` (live), `python -m backend.app.cli grant-role ...`.
API changes: edit `proto/facilityops/v1/*.proto`, run `buf lint && buf generate` (venv active), implement in
`backend/app/api/rpc/`, and add the RPC to `backend/app/api/policy.py` (permissions, never role names; RPCs
without a policy are denied). Never edit `backend/app/gen/`. No REST endpoints except `/health/live`.
Roles/permissions change only through a new Alembic migration; keep `SYSTEM_ROLE_PERMISSIONS` in sync.

## Azure environment
- Use the existing Azure subscription and Foundry project; don't create new ones unless asked.
- Endpoint lives in `.env` (`FOUNDRY_ENDPOINT`), which is gitignored. Never commit keys or endpoints.
- Auth: Entra ID via `DefaultAzureCredential` (the user is signed in with `az login`). No API keys.

## Architecture
**Workflow (deterministic intake pipeline)**, run for every request (text, photo, voice, scanned document):
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
- **Harness:** specialists are `create_harness_agent` agents with the FacilityOps profile (spec §5.3): tool loop
  limit, compaction, run-scoped history, skills, middleware on; todo/modes, file memory, web search, tool
  auto-approval off; never shell, file access or background agents.
- **Knowledge vs tools vs skills:** documents in a Foundry IQ knowledge base on AI Search, reached only through
  read-only tools (Prompt Shields on retrieved text, citations); data through read-only function tools; procedures
  as harness skills.
- **Events/topics:** the team never runs inside an RPC. Transactional outbox → topic → incident worker; ids only in
  events; idempotent consumers; dead letters to human review. PostgreSQL bus locally, Service Bus topic in Azure
  (spec §6.2). Agent Framework has no topics; they sit between runs.
- **Eval harness:** `evals/incidents/` scenarios with fake tools and fault injection; scripted mode gates every PR,
  live mode measures groundedness and tool-call accuracy (spec §10.1).
- Knowledge uses the existing serverless AI Search service (endpoint in `.env` as `AZURE_SEARCH_ENDPOINT`), Entra
  auth. New Azure resources (Service Bus for Phase 3) need the owner's approval first.

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
