# FacilityOps AI — Project Specification

| | |
|---|---|
| **Version** | 0.3 (draft) |
| **Status** | Phase 1 in progress |
| **Last updated** | 2026-10-17 |
| **Related** | [Architecture](architecture.md) · [README](../README.md) |

Requirement status markers used throughout: ✅ implemented · 🟡 partial · ⬜ planned.

## 1. Purpose

FacilityOps AI turns free-form maintenance reports from building occupants ("the heating stopped and it's 14°C",
"water coming through the ceiling") into routed, prioritized work. AI does what it is good at — understanding
language, searching manuals, drafting plans — while deterministic code and people stay responsible for routing,
safety and spending.

The project also serves as a public reference implementation of enterprise AI practice on Microsoft Foundry:
agent orchestration, PII handling, evaluation as a release gate and Responsible AI controls. All data is synthetic.

## 2. Goals and non-goals

**Goals**

1. Classify every non-emergency request by category and priority, and turn routine ones into work orders without manual triage.
2. Route emergencies (gas, fire, flood, carbon monoxide, electrical hazards) to an on-call human with no AI in the path.
3. Investigate complex incidents with a team of specialist agents that produces a grounded, reviewable plan.
4. Keep personal data out of models, logs, analytics and evaluation data, while letting authorized staff see what they need.
5. Make quality measurable: every change is evaluated, and a missed emergency fails the build.

**Non-goals**

- Real tenants, buildings or manuals. The public repository contains synthetic data only.
- Autonomous action. Agents never place orders, dispatch technicians or message tenants on their own.
- Replacing building alarm, fire-panel or BMS systems. This is a reporting and coordination layer.
- Microservices. The backend is a modular monolith (see [Architecture §2](architecture.md#2-backend-structure)).

## 3. Users and roles

**Entra ID authenticates; the application authorizes.** Every caller signs in with Entra ID. On first sign-in the
app creates a user record (keyed by the Entra object id) with the default role, `tenant`. Administrators grant
other roles. Roles map to fine-grained permissions in the database (seeded by migration `0002`), and every
RPC checks a permission rather than a role.

| Role | Permissions | Can |
|---|---|---|
| Tenant | `intake:submit`, `intake:read_own` | Submit requests; see the status of their own requests |
| Technician | `intake:submit`, `work_orders:read_assigned`, `pii:reveal_assigned` | See assigned work orders; reveal location details for assigned work only |
| Facility manager | `intake:*`, `work_orders:*`, `review_queue:manage`, `pii:reveal_all`, `evaluations:read`, `monitoring:read` | Work the review queue; approve work orders and incident plans; reveal PII with audit; read reports |
| Admin | `users:*`, `roles:read`, `audit:read`, `evaluations:read`, `evaluations:run`, `monitoring:read` | Manage users and roles; read the audit log and reports; start evaluation runs |
| On-call responder | *(external rota)* | Receive emergency handoffs; outside this system's AI path |

**Separation of duties:** the admin role controls *access* but holds no operational authority. It cannot reveal
PII, approve work or submit requests. A person who needs both holds both roles, and that assignment is audited.

**Safeguards:** the last active administrator cannot be demoted or disabled; nobody can disable their own account;
disabled users are rejected on every request; every access change writes a security audit event (never PII).
The first administrator is created with `python -m backend.app.cli grant-role`.

## 4. Functional requirements

### 4.1 Intake workflow (Phase 1)

| ID | Requirement | Status |
|---|---|---|
| FR-1 | Accept a request as text via the `IntakeService/SubmitIntake` RPC with a client-supplied `request_id`. | ✅ |
| FR-2 | Accept photo and voice requests and normalize them to text (OCR / speech-to-text) before the pipeline. | ⬜ |
| FR-3 | Screen raw text for emergency signals **before** any redaction or model call; on a match, hand off to on-call and stop. | ✅ |
| FR-4 | Redact PII with Azure AI Language; store the reversible mapping encrypted and separately. | ✅ |
| FR-5 | Check redacted text with Azure AI Content Safety (harm categories) and Prompt Shields (injection). | ✅ |
| FR-6 | Classify category (`hvac`, `plumbing`, `electrical`, `security`, `structural`, `cleaning`, `other`) and priority 1–5 with a schema-validated model response. | ✅ |
| FR-7 | Route to human review when the classifier is unsure (confidence < 0.75 or `requires_human_review`). | ✅ |
| FR-8 | Create a work order; mark it `pending_approval` when priority ≥ 4 (configurable). | ✅ |
| FR-9 | Fail closed: if redaction, safety or classification is unavailable, route to human review. | ✅ |
| FR-10 | Idempotency: repeating a `request_id` returns the original outcome and never creates a second work order. | ✅ |
| FR-11 | Write an audit event for every decision (reason code, never request content). | ✅ |
| FR-12 | Let authorized roles reveal redacted values for a request, with each reveal audited. | ⬜ |
| FR-13 | Human-review queue and approval endpoints for facility managers. | ⬜ |

### 4.2 Complex-incident team (Phase 2)

| ID | Requirement | Status |
|---|---|---|
| FR-20 | Escalate a non-emergency request to the incident team when it meets the escalation criteria (§4.3). | ⬜ |
| FR-21 | A Magentic manager plans, delegates to specialists, tracks progress and re-plans within fixed budgets (§6). | ⬜ |
| FR-22 | Specialists: diagnostician, asset historian, safety & compliance, procurement, scheduler, communicator (§5.2). | ⬜ |
| FR-23 | Produce a structured **incident plan** whose factual claims cite manual sections or records. | ⬜ |
| FR-24 | Facility manager approves, revises or rejects the plan before any side effect. | ⬜ |
| FR-25 | After approval, the application (not an agent) executes actions: purchase request, technician assignment, tenant notice. | ⬜ |
| FR-26 | Checkpoint runs so a run paused for human review can resume. | ⬜ |

### 4.3 Escalation criteria (proposed)

A classified, non-emergency request goes to the incident team if **any** of the following hold; otherwise it stays
in the deterministic path:

- priority ≥ 4 **and** the category is `hvac`, `electrical` or `plumbing` (equipment-centred trades);
- the asset historian's pre-check finds ≥ 2 work orders on the same asset in 30 days (a repeat failure);
- a facility manager escalates the request manually from the review queue.

Emergencies never enter the team, before or after the on-call handoff.

### 4.4 Administration, evaluation and monitoring

| ID | Requirement | Status |
|---|---|---|
| FR-40 | Provision users on first sign-in with a configurable default role; pre-provision staff by Entra object id. | ✅ |
| FR-41 | Admins list, search, disable and re-enable users, and assign roles. | ✅ |
| FR-42 | Permission-based authorization on every RPC, with roles and permissions stored in the database. | ✅ |
| FR-43 | Security audit log of provisioning and access changes, readable by admins. | ✅ |
| FR-44 | Store every evaluation run (CLI, CI or admin-started) with metrics, gates and per-case results by case id. | ✅ |
| FR-45 | Admins start an offline or live evaluation run (`StartEvaluationRun`); one run at a time. | ✅ |
| FR-46 | Metric trends across runs, tagged with model, prompt hash and dataset hash. | ✅ |
| FR-47 | Record every agent run (agent, model, prompt hash, duration, tokens, outcome, error type) via Agent Framework middleware; never prompts or responses. | ✅ |
| FR-48 | Agent report: volume, failure rate, p50/p95 latency, tokens, daily series and error breakdown; production and evaluation traffic kept apart. | ✅ |
| FR-49 | Intake report: outcomes by status, category and priority, human-review and automation rates, top reason codes, pending approvals. | ✅ |
| FR-50 | Cost per run, derived from token counts and model prices. | ⬜ |

### 4.5 Product (Phase 3)

| ID | Requirement | Status |
|---|---|---|
| FR-30 | Tenant web view: submit (text, photo, voice), track status, read notices in their language. | ⬜ |
| FR-31 | Manager web view: review queue, approvals, incident plans with citations, PII reveal. | ⬜ |
| FR-33 | Admin web view: users and roles, audit log, evaluation history and trends, agent and intake dashboards (API: FR-40–49). | ⬜ |
| FR-32 | Infrastructure as code (`azd` + Bicep) using managed identity and Key Vault. | ⬜ |

## 5. Agents

### 5.1 Phase 1: classifier

| | |
|---|---|
| Name | Maintenance Triage Classifier |
| Framework | Microsoft Agent Framework `Agent` with `FoundryChatClient` |
| Input | PII-redacted request text only |
| Output | `Classification` (category, priority, confidence, reason code, requires_human_review), enforced as a response schema |
| Tools | None |
| Instructions | [`maintenance_triage_classifier.md`](../backend/app/prompts/maintenance_triage_classifier.md), including a priority rubric |
| Storage | `store: false`: no server-side conversation retained |

The classifier informs a decision; it never makes one. Routing, thresholds and approvals live in `IntakeCoordinator`.

### 5.2 Phase 2: incident team

Every specialist is read-only. Tools return data; nothing in the team can change the world.

| Agent | Responsibility | Tools (read-only) | Data source | Model tier |
|---|---|---|---|---|
| **Manager** | Builds the task ledger, assigns steps, judges progress, re-plans, writes the final plan | — | — | Large |
| **Diagnostician** | Likely causes and checks, citing manuals | `search_manuals` | Azure AI Search over synthetic manuals | Small |
| **Asset historian** | Asset details, past failures, repeat patterns | `get_asset`, `get_maintenance_history` | PostgreSQL | Small |
| **Safety & compliance** | Lockout/tagout steps, permits, regulatory flags; can mark a plan **blocked** | `get_safety_procedure`, `check_permit_requirements` | Synthetic policy library | Small |
| **Procurement** | Parts, stock, vendor SLA and contract terms, cost estimate | `find_parts`, `get_vendor_terms` | PostgreSQL | Small |
| **Scheduler** | Technicians with the right skills and availability | `find_available_technicians` | PostgreSQL | Small |
| **Communicator** | Draft the tenant notice in the tenant's language | — | Redacted request + plan summary | Small |

Each specialist returns a structured finding: `{claims[], citations[], confidence, open_questions[]}`. A claim
without a citation is shown to the reviewer as unverified.

## 6. Orchestration limits

The Magentic builder in `agent-framework-orchestrations` provides `max_round_count`, `max_stall_count` and
`max_reset_count`. It has no token budget, so the application enforces token, tool-call and time limits with
agent and function middleware.

| Limit | Default | Enforced by | On breach |
|---|---|---|---|
| Coordination rounds | 10 | `max_round_count` | Stop; return partial plan to human review |
| Rounds without progress before re-plan | 2 | `max_stall_count` | Re-plan, then human input on stall |
| Re-plans | 1 | `max_reset_count` | Stop; human review |
| Tokens per run | 60,000 | Middleware summing usage | Terminate; human review |
| Tool calls per specialist per run | 5 | Function middleware | Tool returns a refusal |
| Wall-clock time per run | 120 s | Run timeout | Terminate; human review |

Defaults are starting points to be tuned with evaluation data and cost per incident.

## 7. Human oversight

| Situation | Who decides | Mechanism |
|---|---|---|
| Emergency signal | On-call human | Deterministic handoff, no AI |
| Classifier unsure, or any service failure | Facility manager | Human-review queue |
| Work order priority ≥ 4 | Facility manager | `pending_approval` status |
| Incident plan before execution | Facility manager | Magentic plan review (`enable_plan_review`) |
| Plan with a safety block or cost > threshold | Facility manager | Approval gate in application code |
| Revealing PII | Technician (assigned work) / facility manager | Role check + audit event |

## 8. Data and privacy

- **What is stored:** redacted text, classification, routing outcome, work orders, audit events.
- **PII mapping:** placeholder → original value, encrypted with Fernet (key from Key Vault in Azure), in
  `pii_mappings`, one row per request. Never sent to a model, log, trace or evaluation set.
- **User records:** display name and email of people who sign in, readable only with `users:read`. Audit events
  record ids and role names, never email addresses.
- **Agent runs and eval results:** ids, counts, timings and labels only. Prompts, responses and request text are
  never stored there.
- **Telemetry:** OpenTelemetry spans carry ids, reason codes, model and prompt versions, token counts and latency;
  Agent Framework sensitive-data (message content) capture stays **off**.
- **Retention (proposed):** PII mappings 90 days after a work order closes; audit events 2 years.
- **Azure AI Language** is called with `disable_service_logs=True`.

## 9. Non-functional requirements

| Area | Requirement |
|---|---|
| API | Connect RPC with protobuf contracts in `proto/`; browsers use Connect or gRPC-Web without a proxy; contracts linted and checked for breaking changes in CI |
| Security | Entra ID bearer tokens; permission-based RBAC from the database (RPC policy table, deny by default); managed identity to Azure services; no API keys; secrets in Key Vault; strict CORS and host allow-lists; security headers; request-size limit |
| Abuse | Per-identity rate limiting (HMAC-hashed buckets in PostgreSQL) |
| Reliability | Fail closed to human review; idempotent intake; checkpointed incident runs |
| Latency | Emergency handoff < 1 s (no network calls); routine intake p95 < 8 s |
| Cost | Small models for specialists, budgets in §6; cost per incident reported by eval runs |
| Observability | Agent runs recorded per call (FR-47) for admin reports; traces to Application Insights, where every agent, tool and workflow step is a span (planned) |
| Portability | Schema via Alembic migrations, checked for drift in CI |

## 10. Evaluation and acceptance

Golden set: [`evals/datasets/golden_v1.jsonl`](../evals/datasets/golden_v1.jsonl) (108 synthetic cases).

| Metric | Phase 1 target | Gate | Current |
|---|---|---|---|
| Emergency recall | 1.00 | **Hard: every PR** | 1.00 |
| Category accuracy | ≥ 0.85 | Live eval | 0.91 |
| Priority within ±1 | ≥ 0.90 | Report | 0.92–0.95 |
| Ambiguous → human review | 1.00 | Report | 1.00 |
| Injection resistance | 1.00 | Report | 1.00 |
| Counterfactual consistency | 1.00 | Report | 1.00 |
| Groundedness of incident plans | ≥ 0.90 | Phase 2 (Foundry evaluator) | — |
| Tool-call accuracy | ≥ 0.90 | Phase 2 (Foundry evaluator) | — |

Phase 3 adds a PyRIT red-team report and `RESPONSIBLE_AI.md` (transparency note).

## 11. Phases

| Phase | Scope | Exit criteria |
|---|---|---|
| **1. Intake MVP** | FR-1–13, golden set, CI gate | All Phase 1 FRs ✅; targets in §10 met on two consecutive live runs |
| **2. Incident team** | FR-20–26, synthetic manuals in AI Search, approvals | Scenario tests (success, disagreement, tool failure, budget exhaustion, injection in retrieved content, human rejection) pass; groundedness ≥ 0.90 |
| **3. Product** | FR-30–32, red-team report, transparency note, demo | Deployed with `azd up` from a clean subscription |

## 12. Open questions

1. Should the escalation thresholds in §4.3 be configurable per building?
2. Cost-approval threshold: a fixed amount, or relative to the building's budget?
3. Which languages must tenant notices support at launch? (Golden set covers English, Spanish and French.)
4. Where do photos and voice notes live, and for how long, given they may themselves contain PII?
