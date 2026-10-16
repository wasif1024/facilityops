# FacilityOps AI — Architecture

How requests flow, how agents are orchestrated, where humans stay in control, and how the project's
development skills guide the code that implements all of this. Requirement ids (FR-x) refer to the
[specification](specification.md).

Diagram legend: solid boxes exist today; boxes marked *Phase 2* or *Phase 3* are designed but not built yet.

1. [System context](#1-system-context)
2. [Backend structure](#2-backend-structure) (including the [Connect RPC API](#21-api-connect-rpc))
3. [Orchestration overview](#3-orchestration-overview)
4. [Tier 1: intake workflow](#4-tier-1-intake-workflow)
5. [Tier 2: Magentic incident team](#5-tier-2-magentic-incident-team)
6. [Human oversight](#6-human-oversight)
7. [Trust boundaries and PII](#7-trust-boundaries-and-pii)
8. [Users and access control](#8-users-and-access-control)
9. [Evaluation and agent monitoring](#9-evaluation-and-agent-monitoring)
10. [Development skills](#10-development-skills)
11. [Key decisions](#11-key-decisions)

## 1. System context

```mermaid
flowchart LR
    tenant(["Tenant"])
    tech(["Technician"])
    fm(["Facility manager"])
    oncall(["On-call responder"])

    subgraph app["FacilityOps AI"]
        ui["Web UI<br/><i>Phase 3</i>"]
        api["Backend<br/>Connect RPC · modular monolith"]
        db[("PostgreSQL")]
    end

    subgraph azure["Azure"]
        entra["Entra ID"]
        lang["AI Language<br/>PII detection"]
        cs["AI Content Safety<br/>+ Prompt Shields"]
        foundry["Foundry project<br/>model deployments"]
        search["AI Search<br/>manuals index<br/><i>Phase 2</i>"]
        appi["Application Insights"]
        kv["Key Vault<br/><i>Phase 3</i>"]
    end

    tenant & tech & fm --> ui
    ui -- "Connect / gRPC-Web<br/>protobuf" --> api
    api --> db
    api -- "tokens, app roles" --> entra
    api --> lang & cs & foundry & search
    api -. "OpenTelemetry" .-> appi
    api -. "secrets" .-> kv
    api -- "emergency handoff" --> oncall
```

The backend reaches every Azure service with Entra ID (`DefaultAzureCredential` locally, managed identity in
Azure). There are no API keys.

## 2. Backend structure

FacilityOps is a **modular monolith** with a ports-and-adapters core: one deployable ASGI app and one
database. The domain layer depends on interfaces, never on Azure or SQL directly.

```mermaid
flowchart TB
    subgraph api["api/ — RPC edge"]
        mw["Security middleware<br/>headers, body limit, request id, CORS"]
        icpt["Interceptors<br/>error boundary · auth + policy"]
        routes["rpc/ handlers<br/>Intake · Account · UserAdmin<br/>Evaluation · Monitoring"]
        rl["Rate limiting"]
    end

    subgraph domain["domain/ — pure Python, no I/O"]
        coord["IntakeCoordinator"]
        emerg["Emergency screen"]
        models["Intake models<br/>Pydantic"]
        ports["Ports (Protocols)<br/>PiiRedactor · ContentSafetyInspector<br/>MaintenanceClassifier · WorkOrderWriter<br/>IntakeDecisionWriter"]
    end

    subgraph services["services/ — adapters"]
        azad["Azure adapters<br/>Language · Content Safety · Foundry agent"]
        repo["SqlIntakeRepository"]
    end

    subgraph db["db/ + migrations/"]
        orm["SQLAlchemy models"]
        alembic["Alembic migrations"]
    end

    mw --> icpt --> routes
    routes --> rl
    routes --> coord
    coord --> emerg
    coord --> ports
    azad -. implements .-> ports
    repo -. implements .-> ports
    repo --> orm
    alembic --> orm
```

Because the coordinator only knows the ports, unit tests run the whole workflow with fakes and no network.

### 2.1 API: Connect RPC

The frontend↔backend contract is protobuf, in [`proto/facilityops/v1/`](../proto/facilityops/v1). `buf` generates
the server stubs (Python, committed in `backend/app/gen/`) and, for the Phase 3 web UI, typed TypeScript clients.
The services are [Connect RPC](https://connectrpc.com) applications mounted in the ASGI host, so a single port
speaks three wire protocols.

```mermaid
flowchart LR
    proto[("proto/facilityops/v1/*.proto")]
    proto -- "buf generate" --> pygen["backend/app/gen<br/>messages + service stubs"]
    proto -. "buf generate (Phase 3)" .-> tsgen["frontend/src/gen<br/>TypeScript clients"]

    browser["Browser<br/>connect-web"] -- "Connect or gRPC-Web<br/>HTTP/1.1, no proxy" --> host
    curl["curl / buf curl"] -- "Connect + JSON" --> host
    grpc["gRPC client"] -. "gRPC, needs HTTP/2 server" .-> host

    subgraph host["ASGI host (uvicorn)"]
        direction TB
        mw2["CORS · security headers · body limit"]
        eb["ErrorBoundaryInterceptor"]
        az["AuthInterceptor<br/>RPC → permissions (policy.py)"]
        svc["IntakeService · AccountService · UserAdminService<br/>EvaluationService · MonitoringService"]
        mw2 --> eb --> az --> svc
    end
    tsgen -.-> browser
    pygen --> svc
```

| Design point | Choice |
|---|---|
| Browser transport | Connect protocol or gRPC-Web from `@connectrpc/connect-web`; no Envoy proxy |
| Authorization | One table maps every RPC to required permissions; missing entries are denied, and a unit test fails if any generated RPC lacks one |
| Errors | Connect codes (`permission_denied`, `invalid_argument`, …). Unexpected exceptions become a generic `internal` error, because the library would otherwise send `str(exception)`, which can contain SQL parameters or request text |
| Contract safety | CI runs `buf lint`, checks generated code is current and runs `buf breaking` against `main` on pull requests |
| Plain HTTP | Only `GET /health/live`, for load balancers |

## 3. Orchestration overview

Two tiers, chosen per request. **Tier 1** is a fixed pipeline in plain Python that handles every request.
**Tier 2** is a dynamic Magentic team for the few incidents that need investigation. Agents in both tiers advise.
Code and people decide.

```mermaid
flowchart TB
    req["Request<br/>text · photo · voice"] --> norm["Normalize to text<br/><i>photo/voice: Phase 1 TODO</i>"]
    norm --> e{"Emergency<br/>screen"}
    e -- "gas · fire · flood · CO · electrical" --> oncall["On-call human<br/>AI never involved"]
    e -- "no signal" --> t1

    subgraph t1["Tier 1 · deterministic intake workflow"]
        direction LR
        pii["PII redaction"] --> safe["Content Safety<br/>+ Prompt Shields"] --> cls["Classifier agent<br/>no tools"]
    end

    t1 --> route{"Routing rules<br/>in code"}
    route -- "unsure / service failure" --> hr["Human-review queue"]
    route -- "routine" --> wo["Work order"]
    route -- "priority ≥ 4" --> appr["Work order<br/>pending approval"]
    route -- "meets escalation criteria" --> t2

    subgraph t2["Tier 2 · Magentic incident team · Phase 2"]
        direction LR
        mgr["Manager<br/>plans · re-plans"] <--> spec["6 read-only<br/>specialists"]
    end

    t2 --> plan["Incident plan<br/>with citations"]
    plan --> gate{"Facility manager<br/>approval"}
    gate -- "approved" --> exec["Application executes<br/>purchase · dispatch · notice"]
    gate -- "revise" --> t2
    gate -- "reject" --> hr
```

| | Tier 1: workflow | Tier 2: Magentic team |
|---|---|---|
| Runs for | Every request | Escalated incidents only (spec §4.3) |
| Control flow | Fixed, in `IntakeCoordinator` | Chosen by the manager agent at run time |
| Agents | 1 classifier, no tools | Manager + 6 specialists with read-only tools |
| Side effects | Work order / review case via repository | None. The application executes the approved plan |
| Typical cost | 1 model call | Bounded: ≤ 10 rounds, ≤ 60k tokens, ≤ 120 s |
| Failure mode | Human-review queue | Partial plan to human review |

## 4. Tier 1: intake workflow

Implemented in [`workflow.py`](../backend/app/domain/workflow.py). The order of steps is a safety property:
the emergency screen runs first, on raw text and with no network calls, so neither an Azure outage nor a prompt
injection can delay an emergency.

```mermaid
sequenceDiagram
    autonumber
    actor T as Tenant
    participant API as SubmitIntake RPC
    participant R as Repository
    participant C as IntakeCoordinator
    participant E as Emergency screen
    participant L as AI Language
    participant S as Content Safety
    participant A as Classifier agent

    T->>API: IntakeService/SubmitIntake
    API->>API: auth interceptor, rate limit
    API->>R: begin_request(request_id)
    alt request_id seen before
        R-->>API: stored result
        API-->>T: same outcome, nothing redone
    end
    API->>C: process(command)
    C->>E: detect_emergency(raw text)
    alt emergency signal
        C->>R: on-call handoff + audit
        C-->>T: emergency_handoff
    else no signal
        C->>L: redact
        L-->>C: redacted text + encrypted mapping
        C->>S: analyze text + shield prompt
        C->>A: classify(redacted text)
        A-->>C: Classification (schema-validated)
        C->>C: apply confidence and approval thresholds
        C->>R: work order or review case + audit
        C-->>T: status, category, priority
    end
    Note over C,A: Any exception at L, S or A sends the request to human review (fail closed)
```

## 5. Tier 2: Magentic incident team

*Phase 2.* Built with `MagenticBuilder` from `agent-framework-orchestrations` (not installed yet). The manager is a
`StandardMagenticManager` on a larger model; specialists run on smaller models.

### 5.1 Team and tools

```mermaid
flowchart LR
    subgraph run["Magentic run · budgets enforced by middleware"]
        mgr["Manager<br/>task ledger · progress ledger"]
        diag["Diagnostician"]
        hist["Asset historian"]
        safe["Safety &amp; compliance"]
        proc["Procurement"]
        sched["Scheduler"]
        comm["Communicator"]
    end

    mgr --> diag & hist & safe & proc & sched & comm

    diag -- "search_manuals" --> ais[("AI Search<br/>synthetic manuals")]
    hist -- "get_asset<br/>get_maintenance_history" --> pg[("PostgreSQL")]
    proc -- "find_parts<br/>get_vendor_terms" --> pg
    sched -- "find_available_technicians" --> pg
    safe -- "get_safety_procedure<br/>check_permit_requirements" --> pol[("Policy library")]

    mgr ==> out["Incident plan<br/>claims + citations"]
```

Least privilege is structural: each specialist is built with only its own tools, every tool is read-only, and
the communicator has no tools at all. It drafts a notice, it cannot send one. Nothing in the run can reach the
PII mapping, because it is not exposed by any tool.

### 5.2 The planning loop

The manager keeps two ledgers. The **task ledger** holds facts and the current plan. The **progress ledger** is
re-evaluated each round: is the task done, is progress being made, who speaks next and with what instruction.

```mermaid
sequenceDiagram
    autonumber
    participant APP as Application
    participant M as Manager
    actor FM as Facility manager
    participant SP as Specialist
    participant MW as Budget middleware

    APP->>M: incident (redacted text, classification, asset id)
    M->>M: task ledger: facts + initial plan
    M->>FM: plan review (enable_plan_review)
    FM-->>M: approve or revise
    loop each round, at most max_round_count
        M->>M: progress ledger: done? progressing? next speaker?
        M->>SP: instruction
        SP->>MW: tool call
        MW-->>SP: result, or refusal when over budget
        SP-->>M: finding (claims, citations, confidence)
        opt no progress for max_stall_count rounds
            M->>M: reset and re-plan, at most max_reset_count
        end
    end
    M-->>APP: incident plan
    Note over MW: Token, tool-call and time budgets are app middleware. Magentic has no token limit
    APP->>APP: approval gate (cost and safety flags, in code)
```

### 5.3 Incident lifecycle

```mermaid
stateDiagram-v2
    [*] --> Planning: escalated
    Planning --> PlanReview: initial plan
    PlanReview --> Planning: revise
    PlanReview --> Investigating: approve
    Investigating --> Investigating: round
    Investigating --> Planning: stalled, re-plan
    Investigating --> Drafted: plan complete
    Investigating --> HumanReview: budget exhausted / error
    Drafted --> AwaitingApproval
    AwaitingApproval --> Executing: approved
    AwaitingApproval --> Planning: revise
    AwaitingApproval --> HumanReview: rejected
    Executing --> [*]: application runs actions
    HumanReview --> [*]
```

Runs are checkpointed (`checkpoint_storage`), so a run waiting on a person survives a restart.

## 6. Human oversight

People hold every decision with safety, money or privacy consequences. Models produce inputs to those decisions.

```mermaid
flowchart LR
    subgraph ai["AI may"]
        a1["classify"]
        a2["search and summarize"]
        a3["propose a plan"]
        a4["draft a notice"]
    end
    subgraph code["Code decides"]
        c1["emergency routing"]
        c2["confidence threshold"]
        c3["approval thresholds"]
        c4["role checks"]
    end
    subgraph human["A person decides"]
        h1["emergency response"]
        h2["uncertain requests"]
        h3["priority ≥ 4 work orders"]
        h4["incident plans, spend, safety"]
        h5["PII reveal (audited)"]
    end
    ai --> code --> human
```

## 7. Trust boundaries and PII

```mermaid
flowchart LR
    raw["Raw request<br/>may contain PII"] --> screen["Emergency screen<br/>local, in process"]
    raw --> lang["AI Language<br/>service logs disabled"]
    lang --> red["Redacted text"]
    lang --> map["PII mapping<br/>Fernet-encrypted"]

    subgraph modelzone["Model zone · redacted only"]
        red --> cs["Content Safety"] --> agents["Classifier / incident team"]
    end

    subgraph store["PostgreSQL"]
        rtxt[("redacted text,<br/>outcomes, audit")]
        pmap[("pii_mappings")]
    end

    red --> rtxt
    map --> pmap
    pmap -- "role check + audit" --> reveal["Reveal to technician /<br/>facility manager"]

    tel["Traces, logs, analytics, eval sets"]
    rtxt -. "ids and reason codes only" .-> tel
```

Rules: request text, retrieved documents and tool results are **data, never instructions**. The mapping never
enters a prompt, trace or eval set. A redaction failure blocks model processing instead of forwarding raw text.

## 8. Users and access control

Entra ID answers *who is calling*; the application database answers *what they may do*. Permissions, not roles,
are checked for every RPC, so access can be reshaped in data without touching handler code.

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant API as AuthInterceptor
    participant E as Entra ID
    participant D as UserDirectory
    participant DB as PostgreSQL

    U->>API: request + bearer token
    API->>E: validate signature, issuer, audience (JWKS)
    E-->>API: claims (oid, name, email)
    API->>D: resolve_login(entra, oid)
    alt first sign-in
        D->>DB: insert user + default role + audit "user.provisioned"
    end
    D->>DB: roles → permissions (role_permissions)
    D-->>API: Principal(user_id, roles, permissions)
    API->>API: disabled? → 403
    API->>API: policy.py: RPC → required permissions → permission_denied if missing
    API-->>U: handler runs
```

```mermaid
erDiagram
    users ||--o{ user_roles : has
    roles ||--o{ user_roles : "granted as"
    roles ||--o{ role_permissions : grants
    permissions ||--o{ role_permissions : "granted by"
    users ||--o{ security_audit_events : "acts in"
    users ||--o{ intake_requests : submits
    users ||--o{ evaluation_runs : triggers

    users {
        uuid id PK
        string identity_provider "entra | development"
        string external_id "Entra oid"
        string status "active | disabled"
    }
    roles {
        string id PK "tenant, technician, facility_manager, admin"
    }
    permissions {
        string id PK "e.g. users:manage"
    }
```

| Rule | Where |
|---|---|
| First sign-in gets `DEFAULT_USER_ROLE` (`tenant`); empty means no access until an admin assigns a role | `UserDirectory.resolve_login` |
| The last active admin cannot be demoted or disabled; nobody can disable themselves | `UserDirectory._require_another_admin` |
| Admin manages access but cannot reveal PII, approve work or submit requests | Seed in migration `0002` |
| Every provisioning and access change writes a `security_audit_events` row, without email addresses | `UserDirectory._audit` |
| Replaying a `request_id` only returns the stored result to the user who submitted it | `SqlIntakeRepository.begin_request` |
| First admin is bootstrapped from the CLI, not the API | `python -m backend.app.cli grant-role` |

In local development (`AUTH_ENABLED=false`) the `X-Dev-User` header selects an identity, so different roles can be
tried without Entra. Production refuses to start with authentication disabled.

## 9. Evaluation and agent monitoring

Two streams feed the admin reports. **Agent runs** are recorded by Agent Framework middleware on every call: one row
per run with agent, model, prompt hash, duration, tokens and outcome, never prompts or responses. **Evaluation runs**
store metrics, gates and per-case results by case id. A context variable tags each agent run with the intake request
or evaluation run it served, so production and evaluation traffic are reported separately and an evaluation's model
cost is visible.

```mermaid
flowchart LR
    subgraph sources["Where runs come from"]
        intake["Intake request"]
        cli["CLI / CI<br/>run_eval --store"]
        adminrun["Admin console<br/>StartEvaluationRun"]
    end

    subgraph agent["Any agent"]
        mw["AgentMonitoringMiddleware"]
        llm["Foundry model call"]
        mw --> llm
    end

    scope(["monitoring_scope<br/>request_id or evaluation_run_id"])

    intake --> scope
    cli --> scope
    adminrun -- "background task" --> scope
    scope --> agent

    mw -- "timing, tokens, outcome" --> ar[("agent_runs")]
    cli -- "metrics, gates" --> er[("evaluation_runs")]
    adminrun --> er
    er --> ecr[("evaluation_case_results<br/>case ids only")]
    intake --> ir[("intake_requests")]

    ar --> rep1["MonitoringService<br/>GetAgentReport"]
    ir --> rep2["MonitoringService<br/>GetIntakeReport"]
    er --> rep3["EvaluationService<br/>runs + GetMetricTrend"]
```

| Report | RPC | Shows | Permission |
|---|---|---|---|
| Agent health | `MonitoringService/GetAgentReport` | Runs, failure rate, p50/p95 latency, tokens per agent and model; daily series; error types | `monitoring:read` |
| Agent runs | `MonitoringService/ListAgentRuns` | Individual runs, filterable by agent, status or evaluation run | `monitoring:read` |
| Intake outcomes | `MonitoringService/GetIntakeReport` | Status mix, human-review and automation rates, categories, priorities, top reason codes, pending approvals | `monitoring:read` |
| Evaluation history | `EvaluationService/ListEvaluationRuns`, `GetEvaluationRun` | Runs with metrics and gates; per-case drill-down (`failed_only`) | `evaluations:read` |
| Metric trend | `EvaluationService/GetMetricTrend` | A metric over time, tagged with model and prompt hash | `evaluations:read` |
| Start a run | `EvaluationService/StartEvaluationRun` | Offline or live run in the background; one at a time | `evaluations:run` |

The same middleware applies unchanged to the Phase 2 specialists, which gives per-specialist cost and latency
without new code. OpenTelemetry export to Application Insights (spec §9) is complementary: traces for debugging a
single request, while these tables answer aggregate questions for the admin console.

## 10. Development skills

The repository ships **agent skills** in [`.agents/skills/`](../.agents/skills): short procedures that coding
agents (Claude Code, GitHub Copilot and similar) load when a task touches a given area. They are not part of the
running system. They encode the rules above, so that code written by an AI assistant follows the same safety
properties as the architecture.

### 10.1 Which skill guards which part

```mermaid
flowchart LR
    subgraph skills[".agents/skills"]
        s1["facilityops-<br/>foundry-development"]
        s2["facilityops-<br/>intake-workflow"]
        s3["facilityops-<br/>pii-protection"]
        s4["facilityops-<br/>magentic-team"]
        s5["facilityops-<br/>evaluation"]
    end

    subgraph code["Codebase"]
        k1["services/azure_adapters.py<br/>any Foundry / Agent Framework call"]
        k2["domain/workflow.py<br/>domain/emergency.py"]
        k3["PII redaction, mapping,<br/>telemetry, reveal"]
        k4["Tier 2 team<br/><i>Phase 2</i>"]
        k5["evals/, CI gates"]
    end

    s1 --> k1
    s1 -. "applies to" .-> k4
    s2 --> k2
    s3 --> k3
    s4 --> k4
    s5 --> k5
```

| Skill | Loads when a task… | Key rules it enforces |
|---|---|---|
| `facilityops-foundry-development` | uses Foundry, Agent Framework, Entra ID or Azure AI services | Check current Microsoft docs before writing SDK calls; reuse the existing project; Entra ID only; bound every agent loop; no secrets in tracked files |
| `facilityops-intake-workflow` | changes intake, triage, emergency handling or work orders | Emergency screen before any AI; fail closed; schema-validated classification; idempotent retries; audit without PII |
| `facilityops-pii-protection` | touches redaction, mappings, logs, traces or eval data | Redact before any model or telemetry; mapping behind a role check; audit reveals; test for leaks with planted synthetic identifiers |
| `facilityops-magentic-team` | builds the Phase 2 team | Emergencies never enter; explicit round, token, tool and time limits; read-only least-privilege tools; citations required; no side effects without approval |
| `facilityops-evaluation` | changes datasets, evaluators or CI gates | Synthetic, versioned data; emergency recall is a hard gate; counterfactual fairness pairs; report case ids, not content |

### 10.2 How a skill shapes a change

```mermaid
sequenceDiagram
    autonumber
    actor Dev as Developer
    participant CA as Coding agent
    participant SK as Skill
    participant Repo as Codebase
    participant CI as CI

    Dev->>CA: "add the scheduler specialist"
    CA->>SK: match task to skill descriptions
    SK-->>CA: magentic-team + foundry-development procedures
    CA->>CA: check current Agent Framework docs (skill step)
    CA->>Repo: code with read-only tools, budgets, tests
    CA->>Repo: run unit tests and eval
    Repo->>CI: pull request
    CI->>CI: tests · emergency-recall gate · migration drift check
    CI-->>Dev: pass or fail with case ids
```

Skills and CI back each other up. A skill tells the coding agent the rule, and CI checks the result. For example,
the intake skill requires emergencies to bypass AI, and the eval gate fails the PR if a labelled emergency stops
being routed to on-call.

## 11. Key decisions

| Decision | Why | Trade-off |
|---|---|---|
| Deterministic workflow for intake, agents only for bounded tasks | Safety-critical routing must be testable and auditable | Less flexible than an all-agent design |
| Emergency screen as local rules, before redaction | No outage or injection can delay an emergency; < 1 s | Rules need curation; measured by the recall gate |
| Fail closed to human review | Degraded service never means less protection | More manual work during outages |
| Magentic only for escalated incidents | Dynamic planning is costly; most requests don't need it | Two orchestration styles to maintain |
| Read-only specialists; the application executes | Agent output is never authorization | Extra approval step before action |
| Modular monolith | One team, transactional consistency, simple ops | Scale the whole app together; Tier 2 can move to a worker later |
| Encrypted PII mapping beside redacted text | Operational details recoverable without exposing PII to models or analytics | Key management; reveal endpoint must be audited |
| Entra ID everywhere, no keys | No secrets to leak or rotate in code | Local dev needs `az login` |
| Entra authenticates, the database authorizes | Admins manage roles in-app with an audit trail; fine-grained permissions | App owns role data; first admin needs a CLI bootstrap |
| Connect RPC over protobuf instead of REST | One typed contract generates server stubs and browser clients; gRPC-Web without a proxy; `buf breaking` protects clients | Not browsable like Swagger (use `buf curl`); Python Connect library is still Beta; native gRPC needs an HTTP/2 server |
| Agent monitoring as framework middleware in PostgreSQL | One hook covers every agent; reports join with eval runs and intake | Not a replacement for distributed tracing |
