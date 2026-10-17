# FacilityOps AI — Project Specification

| | |
|---|---|
| **Version** | 0.4 (draft) |
| **Status** | Phase 1 complete (2026-10-17); Phase 2 in progress (Translator FR-60, transparency FR-70–76, escalation and events FR-20/FR-27 done) |
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
| Technician | `intake:submit`, `work_orders:read_assigned`, `work_orders:update_assigned`, `pii:reveal_assigned` | See and complete assigned work orders; reveal details for assigned work only |
| Facility manager | `intake:*`, `work_orders:*`, `review_queue:manage`, `pii:reveal_all`, `evaluations:read`, `monitoring:read` | Work the review queue; approve, reject and assign work orders; reveal PII with audit; read reports |
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
| FR-2 | Accept photo and voice requests and normalize them to text before the pipeline: photo caption + OCR (Azure AI Vision), voice transcription (Azure AI Speech, optional `en`/`es`/`fr` hint). The typed caption is screened for emergencies before any network call, and the combined text again after transcription. | ✅ |
| FR-3 | Screen raw text for emergency signals **before** any redaction or model call; on a match, hand off to on-call and stop. | ✅ |
| FR-4 | Redact PII with Azure AI Language; store the reversible mapping encrypted and separately. | ✅ |
| FR-5 | Check redacted text with Azure AI Content Safety (harm categories) and Prompt Shields (injection; attachment text as documents, FR-19). | ✅ |
| FR-6 | Classify category (`hvac`, `plumbing`, `electrical`, `security`, `structural`, `cleaning`, `other`) and priority 1–5 with a schema-validated model response. | ✅ |
| FR-7 | Route to human review when the classifier is unsure: `requires_human_review`, confidence below 0.70, or a borderline answer (0.70–0.80) that two re-classifications don't confirm. | ✅ |
| FR-8 | Create a work order; mark it `pending_approval` when priority ≥ 4 (configurable). | ✅ |
| FR-9 | Fail closed: if redaction, safety or classification is unavailable, route to human review. | ✅ |
| FR-10 | Idempotency: repeating a `request_id` returns the original outcome and never creates a second work order. | ✅ |
| FR-11 | Write an audit event for every decision (reason code, never request content). | ✅ |
| FR-12 | Let authorized roles reveal redacted values for a request (`RevealRequestDetails`): facility managers for any request, technicians only for work orders assigned to them. Every reveal is audited. | ✅ |
| FR-13 | Human-review queue (emergencies first) where facility managers create a work order or dismiss with a fixed reason code. | ✅ |
| FR-14 | Work-order lifecycle: approve or reject `pending_approval` orders, assign an active technician, complete by the assignee. Every decision is audited with the acting user. | ✅ |
| FR-15 | Tenants list and read their own requests with work-order status; managers list all. | ✅ |
| FR-16 | Accept **scanned documents** (PDF, TIFF, or a phone photo of a page; up to 8 MiB) as `REQUEST_SOURCE_DOCUMENT` and read the first 5 pages with Azure AI Document Intelligence (`prebuilt-read`). | ✅ |
| FR-17 | **Check every uploaded file before any service sees it**: the file's bytes must match the declared type, images must be 50 px to 40 MP, multi-page scans at most 20 pages, PDFs not encrypted. A failed check goes to human review with a reason code (`media_type_mismatch`, `image_too_large`, `document_encrypted`, …). | ✅ |
| FR-18 | **Image content safety**: every photo and image scan is checked by Azure AI Content Safety (hate, sexual, violence, self-harm; severity ≥ 4 is flagged). A flagged image goes to human review (`harmful_image_<category>`) and never reaches the classifier, but the emergency screen still runs on its text first. If the check is unavailable, the request goes to human review (`image_safety_unavailable`). | ✅ |
| FR-19 | **Prompt Shields on attachments**: text taken from a photo or document is checked as a *document* (indirect injection, `document_attack_detected`); the tenant's typed or spoken words as the *user prompt* (`prompt_attack_detected`). | ✅ |

### 4.2 Complex-incident team (Phase 2)

| ID | Requirement | Status |
|---|---|---|
| FR-20 | Escalate a non-emergency request to the incident team when it meets the escalation criteria (§4.3): automatically for priority ≥ 4 in HVAC, electrical or plumbing (the work order is still created, pending approval), or manually by a facility manager (`IncidentService/EscalateRequest`). Emergencies can never be escalated. The repeat-failure criterion arrives with the asset-history tools. | ✅ |
| FR-21 | A Magentic manager plans, delegates to specialists, tracks progress and re-plans within fixed budgets (§6). *[`agents/team.py`](../backend/app/agents/team.py): `MagenticBuilder` with the 7 ledger prompts on gpt-5.2; every round is a decision-trail step.* | ✅ |
| FR-22 | Specialists: diagnostician, asset historian, safety & compliance, procurement, scheduler, communicator (§5.2). *Harness agents on gpt-5.4-mini, each with its own prompt file and only its own tools.* | ✅ |
| FR-23 | Produce a structured **incident plan** whose factual claims cite manual sections or records. *The manager's final answer is validated as an `IncidentPlan`; citations no tool returned in the run are removed, so the claim shows as unverified; an invalid answer goes to a person.* | ✅ |
| FR-24 | Facility manager approves, revises or rejects the plan before any side effect (`IncidentService/DecideIncidentPlan`). The gate is code: a safety-blocked plan can't be approved, a high-cost plan needs explicit confirmation, rejections use fixed reason codes, revisions go back to the team with the feedback and previous plan. | ✅ |
| FR-25 | After approval, the application (not an agent) executes actions: purchase request, technician assignment, tenant notice. | ⬜ |
| FR-26 | Checkpoint runs so a run paused for human review can resume. *Today a revision starts a new run with the previous plan and the feedback (state lives in the database, so nothing is lost); Magentic `checkpoint_storage` for resuming mid-run is a follow-up.* | 🚧 |
| FR-27 | Incidents run off the request path: escalations and plan decisions are published as events (transactional outbox, §6.2) and handled by an incident worker (`python -m backend.app.worker`). Until the team exists, the worker fails closed to a person (`incident_team_unavailable`). | ✅ |
| FR-28 | Equipment manuals and safety procedures are searchable knowledge with section-level citations; retrieved text is screened for indirect prompt injection before an agent sees it (§5.4). *Foundry IQ knowledge base (GA, extractive), `search_manuals` and `get_safety_procedure`, Prompt Shields on every passage; a planted injection is dropped live.* | ✅ |
| FR-29 | A scenario evaluation harness replays incidents against fixture data with fault injection, in CI (scripted models) and live (§10.1). *Scripted harness with 9 scenarios and 5 hard gates runs on every PR (`evals/incidents/`); live mode and tool fixtures arrive with the team.* | 🚧 |

### 4.2.1 Language support with Azure Translator (Phase 2)

Tenants report in English, Spanish or French; staff work in English. Azure AI Translator runs on the existing AI
Services resource with Entra ID, and only ever receives **redacted** text: `[PII_nnnn]` placeholders pass through
translation unchanged (verified live), so the reveal mapping keeps working.

| ID | Requirement | Status |
|---|---|---|
| FR-60 | **Staff translation.** When a request isn't in English, translate its redacted text to English and store it beside the original with the source language. Review queue, request and work-order pages show the translation labelled *Machine translation*, with the original one click away. A translation failure never blocks intake (the original is shown); translation is a convenience, not a safety step. A translation that loses a `[PII_nnnn]` placeholder is discarded. | ✅ |
| FR-61 | **Tenant notices in the tenant's language.** The Communicator drafts a notice in English; a facility manager approves the English text; the application (not an agent) translates the approved text into the tenant's language with Translator and stores both. Tenants see their language with a *machine translated* note. | ⬜ |
| FR-62 | **Translate-then-classify experiment.** An evaluation option classifies the English translation instead of the original. Adopt it only if, on two live runs, category accuracy and route fairness don't drop and label consistency across languages improves (the French priority effect in §10). Otherwise record the result and keep classifying the original. | ⬜ |

Safety steps keep working on the original language: the emergency screen, PII redaction and Prompt Shields all run
before and independently of translation, so a translation error can't hide an emergency or an attack.

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
| FR-47 | Record every agent run (agent, model, prompt hash, duration, tokens, outcome, error type, trace id) via Agent Framework middleware. What was sent and answered is stored separately under FR-71. | ✅ |
| FR-48 | Agent report: volume, failure rate, p50/p95 latency, tokens, daily series and error breakdown; production and evaluation traffic kept apart. | ✅ |
| FR-49 | Intake report: outcomes by status, category and priority, human-review and automation rates, top reason codes, pending approvals. | ✅ |
| FR-50 | Cost per run, derived from token counts and model prices. | ⬜ |

### 4.4.1 Transparency and traceability

Every AI decision must be explainable after the fact: what ran, what it found, what the model was asked and what it
answered, and who acted on it.

| ID | Requirement | Status |
|---|---|---|
| FR-70 | **Decision trail.** Every intake step is recorded per request (`decision_steps`): step, outcome (passed, flagged, failed, skipped, decided), reason code, service and version (model, prompt hash, API version), duration, non-personal details (safety severities, Prompt Shields results, entity categories, the classifier's answer and confidence, confirmation votes, routing thresholds) and trace id. Details never hold text: free-text values are refused in code. | ✅ |
| FR-71 | **Model input and output.** For every model call, store what was sent (redacted text) and what the model answered (`agent_run_contents`), and the exact system instructions behind each prompt hash (`prompt_versions`). Content capture is on by default (`CAPTURE_MODEL_CONTENT`). | ✅ |
| FR-72 | **Access to the trail.** `GetDecisionTrail` requires `decisions:read` (facility managers, administrators); administrators open it from Monitoring → Recent runs → Decision trail (`/decisions/:requestId`). Model inputs and answers are included only with `intake:read_all` (facility managers); administrators see metadata only. Every view of model content is written to the security audit log. | ✅ |
| FR-73 | **"Why this decision" view.** The request page shows staff the steps, model calls and human actions, loaded on demand. | ✅ |
| FR-74 | **Correlated, structured logs.** Every log line carries `request_id`, `trace_id` and `span_id`; `LOG_FORMAT=json` for production. One log line per decision step; logs never contain request text, prompts, answers or exception messages. Trace ids are created locally too, without Application Insights. | ✅ |
| FR-75 | **Tamper-resistant audit.** `intake_audit_events`, `security_audit_events` and `decision_steps` are append-only (database triggers refuse updates and deletes). | ✅ |
| FR-76 | **Retention.** `scripts/db.sh purge-expired` deletes model content after `MODEL_CONTENT_RETENTION_DAYS` (90) and audit records after `AUDIT_RETENTION_DAYS` (730); it is the only path allowed past the append-only triggers. | ✅ |

### 4.5 Product (Phase 3)

| ID | Requirement | Status |
|---|---|---|
| FR-30 | Tenant web view: submit (text, photo, voice, document), track status, read notices in their language. | ⬜ |
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
| Routing | [`ClassificationPolicy`](../backend/app/domain/classification_policy.py): confirmation by agreement in the 0.70–0.80 confidence band (§10) |
| Storage | `store: false`: no server-side conversation retained |

The classifier informs a decision; it never makes one. Routing, thresholds and approvals live in `IntakeCoordinator`.

### 5.2 Phase 2: incident team

Every specialist is read-only. Tools return data; nothing in the team can change the world.

| Agent | Responsibility | Tools (read-only) | Data source | Model tier |
|---|---|---|---|---|
| **Manager** | Builds the task ledger, assigns steps, judges progress, re-plans, writes the final plan | — | — | Large |
| **Diagnostician** | Likely causes and checks, citing manuals | `search_manuals` (step 4), `find_assets`, `get_asset` | Foundry IQ knowledge base over synthetic manuals; PostgreSQL | Small |
| **Asset historian** | Asset details, past failures, repeat patterns | `find_assets`, `get_asset`, `get_maintenance_history` | PostgreSQL | Small |
| **Safety & compliance** | Lockout/tagout steps, permits, regulatory flags; can mark a plan **blocked** | `get_safety_procedure` (step 4), `get_asset`, `check_permit_requirements` | Knowledge base; PostgreSQL | Small |
| **Procurement** | Parts, stock, vendor SLA and contract terms, cost estimate | `get_asset`, `find_parts`, `get_vendor_terms` | PostgreSQL | Small |
| **Scheduler** | Technicians with the right skills and availability (ids and skills, never names) | `find_available_technicians` | PostgreSQL | Small |
| **Communicator** | Draft the tenant notice in the tenant's language | — | Redacted request + plan summary | Small |

Each specialist returns a structured finding: `{claims[], citations[], confidence, open_questions[]}`. A claim
without a citation is shown to the reviewer as unverified.

The manager is Agent Framework's `StandardMagenticManager`. Specialists are created with `create_harness_agent`
and the FacilityOps harness profile (§5.3).

**Prompts** ([`backend/app/prompts/`](../backend/app/prompts)), one file per agent: `incident_manager.md`,
`incident_manager_ledgers.md` (the seven Magentic planning and progress prompts, keeping the framework's
progress-ledger JSON schema; the final answer is the `IncidentPlan` schema), `asset_historian.md`, `diagnostician.md`,
`safety_compliance.md`, `procurement.md`, `scheduler.md`, `communicator.md`. Each has the role, the exact tools,
a procedure, constraints, a JSON output contract and worked examples with realistic tool results. Unit tests
(`test_prompts.py`) check that ledger prompts format with exactly their placeholders, every JSON example parses (the
manager's example validates as an `IncidentPlan`), each specialist's tool table matches the registry, and every
citation in an example exists in the synthetic world or knowledge base.

### 5.3 Agent runtime: the harness profile

Agent Framework's **harness** (`create_harness_agent`, released July 2026) is a ready-made agent runtime: tool-calling
loop, history persistence, context compaction, todo and plan modes, file memory, skills, web search, tool
auto-approval and OpenTelemetry. Every feature can be switched off. FacilityOps keeps the features that make a
bounded specialist reliable and switches off everything that would let an agent act, remember or reach outside its
tools.

| Harness feature | Setting | Why |
|---|---|---|
| Tool-calling loop | On; **5 calls per specialist per incident, enforced by our tool runtime** (`run_tool`) | The harness's `loop_max_iterations` only applies to its optional looping feature, so the budget lives in our code, where it is also recorded |
| Harness instructions | **Replaced** by [`specialist_harness.md`](../backend/app/prompts/specialist_harness.md) | The default tells agents to "perform actions" and narrate; ours says read-only, data-not-instructions, JSON only |
| History | In memory, scoped to one run (the harness default) | Nothing is written to disk; what each model saw and said is captured by the monitoring middleware (FR-71) |
| Context compaction | Off for now (no token limits passed) | A run is a few short turns per specialist; switch on with `max_context_window_tokens` if runs grow |
| Skills | Not used yet | Procedures come from the knowledge base (§5.4); skills can be added for reusable know-how later |
| Middleware | Token budget + monitoring (FR-47, FR-71) | Stops the run when the incident's tokens are spent; records every call, with content, including streamed runs |
| OpenTelemetry | On, sensitive data off | GenAI spans without content (§8) |
| Todo list, plan/execute modes | **Off** (`disable_todo`, `disable_mode`) | The Magentic manager owns the plan; two planners would disagree |
| File memory | **Off** (`disable_file_memory`) | On by default it writes notes to an `agent-file-memory` folder on disk; no state may outlive the run |
| Web search | **Off** (`disable_web_search`) | Answers come from synthetic manuals and records only; the open web is an injection and grounding risk |
| Tool auto-approval | **Off** (`disable_tool_auto_approval`) | Tools are read-only; approvals of actions are application code (§7), never a heuristic |
| Background agents, file access, shell, looping | **Never** (experimental, and outside least privilege) | |

### 5.4 Knowledge, tools and skills

Three ways a specialist gets what it needs, kept separate:

| | What it is | Examples | Where it lives |
|---|---|---|---|
| **Knowledge** | Facts retrieved from documents, with citations | Equipment manuals, lockout/tagout procedures, vendor contracts | A **Foundry IQ knowledge base** on the project's existing serverless Azure AI Search service, reached only through the `search_manuals` / `get_safety_procedure` tools, with Entra ID (no query keys) |
| **Tools** | Read-only functions over FacilityOps data | `get_maintenance_history`, `find_parts`, `find_available_technicians` | Python, in `backend/app/agents/tools/`, running with the incident's permissions |
| **Skills** | Procedures the agent loads on demand | "Cite manual sections", "Assess a repeat failure" | Markdown files, harness `skills_paths` |

**How the tools run** ([`agents/tools/`](../backend/app/agents/tools)): each call opens a `SET TRANSACTION READ ONLY`
transaction, so the database refuses a write whatever the tool's code does; each specialist has 5 calls per run and
then gets a refusal it can read; every call is a decision-trail step (tool, specialist, outcome, duration, argument
codes, result count; free-text arguments are recorded as a length only); errors return only their type. Every result
carries a citation (`asset:…`, `work_order:…`, `part:…`, `contract:…`, `technician:…`, `permit:…`). Data comes from
the synthetic world in [`fixtures/synthetic_world.json`](../backend/app/fixtures/synthetic_world.json)
(`scripts/db.sh seed-world`): 2 buildings, 6 assets, maintenance history (one repeat failure), parts, vendors with
SLAs, technician shifts and permits (one expired).

**Knowledge base** ([`agents/knowledge.py`](../backend/app/agents/knowledge.py), `scripts/knowledge.sh`): 9 synthetic
documents in [`fixtures/knowledge/`](../backend/app/fixtures/knowledge) (5 manuals, 3 procedures, 1 deliberately
poisoned "community tip"), one passage per section, in a Foundry IQ knowledge base on the existing serverless
search service. It uses the generally available API (2026-04-01) in minimal, extractive mode: passages come back and
our agents reason over them, so no model runs inside the search service and it needs no extra identity. Untrusted
notes are screened and never returned.

All documents are synthetic. Retrieved passages pass Prompt Shields' document-attack check before they reach a
model; a flagged passage is dropped and recorded as a reason code. Each tool call is checked against the per-run
tool budget and recorded (tool name, duration, outcome; never arguments that could contain text).

## 6. Orchestration limits and events

### 6.1 Budgets

`MagenticBuilder` (`agent_framework.orchestrations`) provides `max_round_count`, `max_stall_count` and
`max_reset_count`. They are always set explicitly: recent releases default the round and reset limits to
unlimited. Magentic has no token budget, so the application enforces tokens and time itself, and the harness
loop limit caps tool calls.

| Limit | Default | Enforced by | On breach |
|---|---|---|---|
| Coordination rounds | 10 | `max_round_count` | Stop; return partial plan to human review |
| Rounds without progress before re-plan | 2 | `max_stall_count` | Re-plan, then human input on stall |
| Re-plans | 1 | `max_reset_count` | Stop; human review |
| Tokens per run | 150,000 | `TokenBudgetMiddleware` on every agent | Terminate; human review (`token_budget_exhausted`) |
| Tool calls per specialist per run | 5 | Tool runtime (`run_tool`) | Tool returns a refusal the model can read |
| Wall-clock time per run | 240 s | Team timeout | Terminate; human review (`team_timeout`) |
| Delivery attempts per event | 3 | Event consumer | Dead-letter; human review |

Defaults are starting points to be tuned with evaluation data and cost per incident.

**Tuned from the first live runs (2026-10-17).** With 60k tokens and 120 s, a real boiler incident stopped after four
of six specialists (65k tokens, 74 s): the manager's context grows every round, and each specialist resends its prompt
and tool results on each tool step. A complete run then took **7 rounds, 11 tool calls, 119k tokens and 110 s**
(manager on gpt-5.2, specialists on gpt-5.4-mini), so the limits are now 150k tokens and 240 s.

### 6.2 Events and topics

A Magentic run takes tens of seconds and may wait hours for a person, so it never runs inside an RPC. The request
path records a decision and publishes an **event**; an **incident worker** subscribed to the relevant topic does the
work. Agent Framework itself has no topics (agents inside a run talk through workflow edges), so topics sit
*between* runs, at the application level.

| Event | Published when | Consumed by |
|---|---|---|
| `intake.escalated` | A request meets the escalation criteria (§4.3) or a manager escalates it | Incident worker: starts a run |
| `incident.plan_ready` | A run produces a plan, or stops on a budget or error | Review queue (manager notified) |
| `incident.plan_decided` | A manager approves, revises or rejects a plan | Incident worker: resumes from checkpoint on *revise*; action executor on *approve* |
| `incident.closed` | Actions finish or the incident is rejected | Reporting |

Rules:

- **Transactional outbox.** An event is written to `outbox_events` in the same database transaction as the state
  change, then dispatched. No state change without its event, and no event without its state change.
- **Ids only.** Events carry ids, types and reason codes, never request text or PII. Consumers read what they need
  from the database with their own permissions.
- **At-least-once, idempotent consumers.** Each consumer records the event ids it handled; a redelivery is a no-op.
- **Dead letters go to people.** After 3 failed deliveries the event is dead-lettered and the incident goes to
  human review, like any other failure.
- **Two transports, one port.** Locally and in tests the bus is PostgreSQL (`SELECT … FOR UPDATE SKIP LOCKED`
  on the outbox), so nothing new is needed to run the project. In Azure the dispatcher publishes to an **Azure
  Service Bus topic** (`facilityops-events`) with one subscription per consumer, filtered by event type,
  connected with managed identity.

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
- **Photos, documents and voice notes:** sent only to Azure AI Content Safety (images), Vision, Document
  Intelligence and Speech, never to a language model, and never stored. Extracted text goes through the emergency
  screen in full, is then capped (5,000 characters, and within the 10,000-character stored limit) and redacted like
  typed text. Scanned forms often carry names and unit numbers, which is exactly what redaction is for.
- **PII mapping:** placeholder → original value, encrypted with Fernet (key from Key Vault in Azure), in
  `pii_mappings`, one row per request. Never sent to a model, log, trace or evaluation set.
- **User records:** display name and email of people who sign in, readable only with `users:read`. Audit events
  record ids and role names, never email addresses.
- **Agent runs and eval results:** ids, counts, timings and labels only.
- **Model inputs and answers (FR-71):** what each model was sent and answered, in `agent_run_contents`. Models only
  ever receive redacted text, so this holds placeholders, not personal data. Readable only with `intake:read_all`,
  every view audited, purged after 90 days.
- **Decision trail (FR-70):** steps, outcomes, versions and non-personal details per request; append-only, kept two
  years.
- **Telemetry and logs:** OpenTelemetry spans and log lines carry ids, reason codes, model and prompt versions,
  token counts and latency, never content. Agent Framework sensitive-data capture stays **off**: Application
  Insights is usually readable by more people (operations, developers) than the database, so content stays in the
  access-controlled database and traces link to it by trace id.
- **Retention:** model inputs and answers 90 days, decision trail and audit events 2 years (`scripts/db.sh purge-expired`);
  PII mappings 90 days after a work order closes (proposed).
- **Azure AI Language** is called with `disable_service_logs=True`.

## 9. Non-functional requirements

| Area | Requirement |
|---|---|
| API | Connect RPC with protobuf contracts in `proto/`; browsers use Connect or gRPC-Web without a proxy; contracts linted and checked for breaking changes in CI |
| Security | Entra ID bearer tokens; permission-based RBAC from the database (RPC policy table, deny by default); managed identity to Azure services; no API keys; secrets in Key Vault; strict CORS and host allow-lists; security headers; request-size limit |
| Abuse | Per-identity rate limiting (HMAC-hashed buckets in PostgreSQL) |
| Reliability | Fail closed to human review; idempotent intake; checkpointed incident runs; transactional outbox with idempotent consumers and dead-lettering to human review |
| Latency | Emergency handoff < 1 s (no network calls); routine intake p95 < 8 s |
| Cost | Small models for specialists, budgets in §6; cost per incident reported by eval runs |
| Observability | Agent runs recorded per call (FR-47); decision trail and model input/output (FR-70, FR-71); structured logs correlated by request and trace id (FR-74). OpenTelemetry to Application Insights: RPC spans, Azure service calls, one span per intake step with its outcome, Agent Framework GenAI spans (model, tokens). No content in traces or logs; exception messages never recorded or logged |
| Portability | Schema via Alembic migrations, checked for drift in CI |

### 9.1 Guardrails on intake

| Layer | Guardrail | On failure |
|---|---|---|
| Before anything | Emergency screen on typed text (local rules, no network) | On-call handoff |
| File | Signature matches declared type; image 50 px–40 MP; scan ≤ 20 pages; PDF not encrypted; ≤ 8 MiB | Human review, reason code |
| Image | Content Safety image analysis, severity ≥ 4 in any category | Human review; never classified |
| Extracted text | Emergency screen again on the full text | On-call handoff |
| Text | PII redaction before any model, log or trace | Human review if unavailable |
| Text | Content Safety harm categories on all text; Prompt Shields: user prompt + documents | Human review |
| Model | No tools; schema-validated output; confirmation band; routing in code | Human review |
| Abuse | Per-user rate limit; request size limit; deny-by-default RPC permissions | Rejected |

Every safety step fails closed. A flagged or unreadable request always reaches a person; nothing is silently dropped.

## 10. Evaluation and acceptance

Golden set: [`evals/datasets/golden_v1.jsonl`](../evals/datasets/golden_v1.jsonl) (108 synthetic cases).

| Metric | Phase 1 target | Gate | Current (two runs) |
|---|---|---|---|
| Emergency recall | 1.00 | **Hard: every PR** | 1.00, 1.00 |
| Category accuracy | ≥ 0.85 | Live eval | 0.91, 0.91 |
| Priority within ±1 | ≥ 0.90 | Report | 0.94, 0.94 |
| Ambiguous → human review | 1.00 | Report | 1.00, 1.00 |
| Injection resistance | 1.00 | Report | 1.00, 1.00 |
| **Counterfactual route consistency** (modal over 5 samples) | 1.00 | Live eval (`--min-route-fairness`) | 1.00, 1.00 |
| Counterfactual label consistency (single sample) | tracked | Report | 0.75, 0.75 (F02, see below) |
| Route self-agreement (5 samples) | tracked | Report | 0.83, 0.89 |
| Automation rate · accuracy of automated cases | tracked | Report | 0.90 · 0.96, 0.91 · 0.94 |
| Classifier calls per request | tracked | Report | 1.70, 1.73 |
| Groundedness of incident plans | ≥ 0.90 | Phase 2 (§10.1) | — |
| Tool-call accuracy | ≥ 0.90 | Phase 2 (§10.1) | — |

"Current" is two consecutive live runs on 2026-10-17 (`2b0f21ce`, `6c9edb7e`): complete pipeline with PII
redaction, gpt-5.2, prompt `2f091038960e`, confirmation policy below, every case classified five times.

**Why fairness is judged on routes and modal answers.** gpt-5.2 is not deterministic, even with
`temperature: 0` and a fixed `seed`, and redaction makes name variants *identical text* (every name becomes
`[PII_0001]`). A single-sample comparison therefore mixes bias with chance. The route is what decides how a
tenant is treated (automatic work order, manager approval or human review); priority within a route only orders
the queue. The gate asks: does every variant of a request take the same route on its most common answer?

**The confirmation policy** ([`classification_policy.py`](../backend/app/domain/classification_policy.py)).
Measuring five samples per case showed that route flips came almost entirely from *confidence*, not priority: the
model reports a few habitual values (0.86, 0.78, 0.74), and a single threshold at 0.75 sat between 0.74 and 0.78,
so identical requests went to a person or not by chance. Now answers at 0.80 or above are automated, below 0.70 go
to a person, and in between the request is classified twice more and automated only if all three agree on
category and priority (`classified_by_agreement`; otherwise `classification_unconfirmed`).

| | Before (single threshold 0.75) | After (confirmation band 0.70–0.80) |
|---|---|---|
| Requests automated | 73–75% | 89% |
| Accuracy of automated requests | 94.5% | 94.0% |
| Same route on identical input | 63–66% | 83–89% |
| Route fairness (modal) | 1.00, **0.75** | 1.00, 1.00 |
| Classifier calls per request | 1.0 | 1.7 |

Agreement turned out to be better evidence than the model's stated confidence: more requests are automated at the
same accuracy, and fewer land on a reviewer by chance. The cost is about 70% more classifier calls. The band and
the number of confirmations are settings (`CLASSIFICATION_BAND_LOW`, `CLASSIFICATION_BAND_HIGH`,
`CLASSIFICATION_CONFIRMATIONS`; 0 restores the single threshold).

**Known limitation: a language effect on priority.** In fairness group F02 (a running toilet) the French variant
was priority 3 in every sample of every run, while the English variants were mostly 2. The difference is
systematic, not chance, but it stays within one route (automatic work order), so tenants are treated the same
way; only queue order differs. It is tracked by the label-consistency metric and will be disclosed in
`RESPONSIBLE_AI.md`. A fix would need more multilingual cases than the golden set has today, rather than a prompt
tweak fitted to one example.

**Media intake** ([`evals/run_media_eval.py`](../evals/run_media_eval.py), live): synthetic scans and photos drawn
in code, through the real services. 6/6 pass on 2026-10-17: a scanned form is read and classified, and the fake
name and phone number drawn on it are absent from the stored text (the eval fails on any planted identifier that
survives redaction); a faint
instruction to the AI inside a scan is caught as `document_attack_detected`; a gas emergency written in a scan is
handed to on-call; a phone scan and a photo of a sign are read; a PDF declared as a PNG is refused. There is no
harmful-image case on purpose (no violent or sexual pictures in a public repository); that path is covered by unit
tests with a fake inspector.

**Known limitation: form fields.** Azure AI Language finds names and numbers in sentences more reliably than in
form layouts: on a scanned form, `Unit: 12C` stayed visible while "unit 12C" in a sentence is redacted. Unit numbers
are operational details a technician needs, but a form-aware pass (Document Intelligence key-value pairs, then
redaction of known personal fields) would be stricter. Tracked for `RESPONSIBLE_AI.md`.

### 10.1 Phase 2: incident evaluation harness

The golden set tests one model call. An incident run is many calls, tools and decisions, so Phase 2 adds a
**scenario harness** in `evals/incidents/`: each scenario is a synthetic incident plus a fixture world (assets,
maintenance history, parts, technicians, manual passages) and its expected outcome. Tools are replaced by fakes that
serve the fixture data, record every call, and can inject faults.

| Scenario family | What it checks |
|---|---|
| Success | A grounded plan with the expected actions |
| Specialist disagreement | The manager reconciles or surfaces the conflict |
| Tool failure / timeout | Partial plan to human review, never a guess |
| Budget exhaustion | Run stops at the limit and goes to human review |
| Injection in retrieved content | Poisoned manual passage is dropped; no instruction is followed |
| Safety block | Lockout/tagout requirement blocks the plan |
| Human rejection or revision | Run resumes from its checkpoint, or the incident closes |
| Emergency mis-escalation | An emergency never reaches the team |

**Today** ([`evals/incidents/`](../evals/incidents)): the scripted team returns canned plans, reasons or errors, and the
real worker, outbox, decision service and trail run around it. 9 scenarios (success, revision, rejection, safety
block, high cost, grounding, tool failure, budget exhaustion, emergency) and 5 hard gates
(`no_side_effects_before_approval`, `emergency_never_enters`, `events_ids_only`, `no_stuck_incidents`,
`trail_complete`) pass on every PR. Run locally with `python -m evals.run_incident_scenarios`.

It runs in two modes. **Scripted** mode replaces the models with a scripted chat client, so it is deterministic,
free and runs on **every PR**: it tests the harness, budgets, events and gates, not model quality. **Live** mode
uses the real models, stores an evaluation run like Phase 1, and adds Foundry evaluators.

| Metric | Target | Gate |
|---|---|---|
| Side effects before approval | 0 | **Hard: every PR** (scripted) |
| Budget adherence (runs that stop at their limits) | 1.00 | **Hard: every PR** (scripted) |
| Emergencies reaching the team | 0 | **Hard: every PR** (scripted) |
| Safety-block recall | 1.00 | Live eval |
| Groundedness of incident plans | ≥ 0.90 | Live eval (Foundry evaluator) |
| Tool-call accuracy | ≥ 0.90 | Live eval (Foundry evaluator) |
| Claims with a citation | ≥ 0.95 | Report |
| Cost and latency per incident | tracked | Report |

Phase 3 adds a PyRIT red-team report and `RESPONSIBLE_AI.md` (transparency note).

## 11. Phases

| Phase | Scope | Exit criteria |
|---|---|---|
| **1. Intake MVP** ✅ | FR-1–19, golden set, media eval, CI gate | All Phase 1 FRs ✅; every target in §10 met on two consecutive live runs (2026-10-17) |
| **2. Incident team** | FR-20–29, FR-60–62 (Translator): Magentic team on the harness profile, knowledge base over synthetic manuals, events with outbox and incident worker, scenario harness, approvals | Every scenario family in §10.1 passes in scripted mode on every PR; live targets in §10.1 met on two consecutive runs |
| **3. Product** | FR-30–32, Service Bus topic in Bicep, red-team report, transparency note, demo | Deployed with `azd up` from a clean subscription |

## 12. Open questions

1. Should the escalation thresholds in §4.3 be configurable per building?
2. Cost-approval threshold: a fixed amount, or relative to the building's budget?
3. Which languages must tenant notices support at launch? (Golden set covers English, Spanish and French.)
4. ~~Where do photos, documents and voice notes live?~~ Decided: nowhere. Raw media is transcribed in memory and discarded; only the redacted transcript is stored.
5. ~~Which search service?~~ Decided: the **existing** serverless Azure AI Search service beside the Foundry
   resource (Foundry IQ Serverless), reached with Entra ID; no new resource. Phase 3 still needs a **Service Bus**
   namespace, which needs the owner's approval before it is created.
6. ~~Foundry IQ or direct AI Search queries?~~ Decided (2026-10-17): a **Foundry IQ knowledge base**, behind our own
   tool wrapper, so budgets, injection screening and audit stay in our code. Check the current knowledge-base API
   (and the preview status of the serverless tier) before building.
