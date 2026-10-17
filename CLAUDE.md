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
OpenTelemetry to App Insights (no content). Transparency: decision trail per request (decision_steps, adapters call
`note(...)` with codes/counts only), model input/output in agent_run_contents (redacted; intake:read_all; views
audited), prompt_versions, GetDecisionTrail (decisions:read), append-only audit triggers, `scripts/db.sh
purge-expired`, structured logs with request_id/trace_id (core/logs.py). Every new agent or step must write to the
trail. Phase 2 in progress: FR-60 (Translator), FR-70–76 (transparency), FR-20/FR-27 (escalation, outbox, event bus,
incident worker: services/events.py, services/incident_worker.py, worker.py; UnavailableIncidentTeam fails closed)
done; incident plans (domain/incident_plan.py, versioned incident_plans, DecideIncidentPlan with safety/cost
gate in code) and the scripted scenario harness (evals/incidents/, 9 scenarios, 5 gates, runs in the integration
suite) done. The team returns TeamOutcome(plan|reason) and never writes or acts. Next: synthetic fixture world +
read-only tools (done: backend/app/agents/tools/, runtime.run_tool = READ ONLY txn + budget + trail step;
registry.SPECIALIST_TOOLS least privilege; world in backend/app/fixtures/synthetic_world.json, `scripts/db.sh
seed-world`) and Foundry IQ knowledge (done: agents/knowledge.py, GA 2026-04-01 extractive, no model in search;
`scripts/knowledge.sh setup|search`; Prompt Shields on every passage, notes never returned) done. Agent prompts written (8 files,
agents/prompts.py loader, tests/unit/test_prompts.py keeps them consistent with tools and fixtures). Magentic team done:
agents/team.py (MagenticIncidentTeam; manager gpt-5.2 + harness specialists gpt-5.4-mini; budgets 10 rounds,
250k tokens, 240 s; invented citations removed), agents/scripted.py (ScriptedChatClient for tests), wired in
worker.py. Approved actions done: services/incident_actions.py (consumer action-executor; incident_actions table;
plan actions carry technician_id / part_sku / quantity). Incident screens done: frontend/src/features/incidents/
(panel on request + work order pages, escalate in review queue; tenants see incident_status only).
Tenant notices done (FR-61): services/tenant_notices.py, tenant_notices table, SendTenantNotice; plan.tenant_notice is
the communicator's draft; AzureTranslator.translate(target_language=...). FR-62: `run_eval --translate-first`, measured, not adopted (spec §10).
Live incident eval built (evals/incidents/live.py, `run_incident_scenarios --mode live [--store]`), not yet run in
full (cost; owner deferred it). Agents run with store=False. Phase 3 in progress: web views FR-30/31/33 done; infra/ (azd + Bicep, validated with `az deployment sub validate`,
NOT deployed: needs the owner's go-ahead, creates paid resources; docs/deployment.md), db/engine.py (Entra token
DB auth), services/service_bus.py (SERVICE_BUS_NAMESPACE), RESPONSIBLE_AI.md done. Next: azd up (approval), red-team
scan (cost, approval), demo GIF. FR-26 checkpoint resume is a follow-up.
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

Demo accounts (alice tenant, fm, tech, local-developer admin; no passwords, dev identities) are created by
migration 0004 unless APP_ENV=production or SEED_DEMO_ACCOUNTS=false; `scripts/db.sh remove-demo-accounts` /
`add-demo-accounts`; keep backend/app/services/demo_accounts.py in step with the migration's frozen list.
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
- **Translator (Phase 2, important; FR-60 done):** Azure AI Translator on the existing AI Services resource (Entra, path
  `/translator/text/v3.0/translate`), redacted text only, placeholders survive. FR-60 staff translation to English,
  FR-61 approved tenant notices into the tenant's language (application translates, not an agent), FR-62
  translate-then-classify only if the fairness eval improves (spec §4.2.1).

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
