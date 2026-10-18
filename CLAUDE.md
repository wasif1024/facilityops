# FacilityOps AI

Portfolio project: an AI-assisted facility management system on Microsoft Foundry and Azure that shows agent
orchestration, enterprise AI practices, PII handling, evaluation and Responsible AI. It is public on the owner's
GitHub, so the README, diagrams and demo quality matter. Requirements and results live in
[docs/specification.md](docs/specification.md); design in [docs/architecture.md](docs/architecture.md).

## What exists
- **Intake pipeline** (`backend/app/domain/workflow.py`, deterministic): declared emergency or local emergency rules
  first (`domain/emergency.py`, no AI), media checks and reading (Vision, Speech, Document Intelligence), PII
  redaction (Azure AI Language), Content Safety + Prompt Shields, Foundry classifier, routing in code, work order,
  approval for priority ≥ 4. Every step writes to the decision trail (`note(...)` with codes and counts only).
- **Incident team** (`backend/app/agents/`): Magentic manager (gpt-5.2) and six harness specialists (gpt-5.4-mini)
  with read-only tools and a Foundry IQ knowledge base. Runs off the request path via the outbox and the incident
  worker. Budgets in `TeamBudgets` (tests keep the docs in step). The team returns a plan and never acts: a facility
  manager decides (`DecideIncidentPlan`, safety and cost gate in code), then the application carries out actions
  (`services/incident_actions.py`) and translates approved tenant notices (`services/tenant_notices.py`).
- **Frontend** (`frontend/`): React 19 + TS + Vite + Tailwind 4 + shadcn/ui, Node 24 via `.nvmrc`, gRPC-Web. UI shows
  actions by permission from `GetMe`; revealed PII lives only in component state.
- **Identity**: residents on Entra External ID (provider `entra_external`, default role tenant), staff on workforce
  Entra ID (no default role); `backend/app/api/token_validation.py`, docs/identity-setup.md. Entra authenticates,
  the database authorizes.
- **Azure deployment**: `azure.yaml` + `infra/` (Bicep), docs/deployment.md. Validated, not deployed: `azd up` creates
  paid resources and needs the owner's go-ahead.
- **Not done** (deliberately, cost): live incident evaluation run, red-team scan, deployment; FR-26 checkpoint resume.

## Commands (from the repo root)
- Run: `scripts/dev.sh` (API + worker + web; `backend`, `worker`, `frontend` alone; `--entra`). It wraps
  `uvicorn backend.app.main:create_app --factory` (no module-level app). Or `docker compose up --build -d`.
- Database: `scripts/db.sh` (no args = create the database if needed and migrate), `up|down|status|new "msg"|check|
  reset|sql|seed-world|remove-demo-accounts|add-demo-accounts|purge-expired`. User-facing docs show only `db.sh`,
  never raw Alembic. New migrations are named `NNNN_slug.py`.
- Tests: `python -m pytest -q backend/tests/unit`, `python -m pytest -q backend/tests/integration` (local
  PostgreSQL), `cd frontend && npm run test`.
- Evals: `python -m evals.run_eval [--mode live] [--store]`, `python -m evals.run_emergency_eval`,
  `python -m evals.run_media_eval` (live), `python -m evals.run_incident_scenarios [--mode live]`.
- CLI: `python -m backend.app.cli grant-role ...`.

## Rules
- **API changes**: edit `proto/facilityops/v1/*.proto`, run `buf lint && buf generate` (venv active) and
  `npm run generate` in `frontend/`, implement in `backend/app/api/rpc/`, add the RPC to `backend/app/api/policy.py`
  (permissions, never role names; RPCs without a policy are denied). Never edit `backend/app/gen/`. No REST
  endpoints except `/health/live`.
- **Roles and permissions** change only through a new Alembic migration; keep `SYSTEM_ROLE_PERMISSIONS` in sync.
- **Demo accounts** (alice, fm, tech, local-developer) come from migration 0004, never in production; keep
  `backend/app/services/demo_accounts.py` in step with its frozen list.
- **Agent code**: check the current Microsoft Agent Framework and Foundry SDK APIs first (they change quickly);
  one prompt file per agent in `backend/app/prompts/`; every new agent or step writes to the decision trail.
- **Events**: the team never runs inside an RPC; ids only in events; idempotent consumers; dead letters go to a
  person. PostgreSQL outbox locally, Service Bus in Azure.
- **PII**: redacted text plus a protected mapping only authorized roles can reveal; logs, traces, analytics and
  eval data never hold PII.
- **Synthetic data only**: fake buildings, tenants and manuals. Nothing real in the public repo.
- **Commit only when the user asks.**

## Azure environment
- Use the existing Azure subscription and Foundry project; don't create new resources without the owner's approval.
- Endpoints live in `.env` (gitignored, e.g. `FOUNDRY_ENDPOINT`). Never commit keys, endpoints or resource names.
- Auth: Entra ID via `DefaultAzureCredential` (`az login`). No API keys.
