# FacilityOps AI

AI-assisted facility maintenance intake on **Microsoft Foundry** and the **Microsoft Agent Framework**.
Tenants report problems in plain language; a deterministic pipeline screens, redacts, classifies and
routes each request, and keeps humans in charge of anything dangerous or expensive.

The project is a worked example of enterprise AI practice: fail-closed orchestration, PII that is
redacted but recoverable by authorized roles, an evaluation suite with a hard CI gate, and Responsible AI
checks for prompt injection and counterfactual fairness. All data in the repository is synthetic.

> **Status:** Phase 1 (intake MVP) in progress. See [Roadmap](#roadmap).
>
> **Docs:** [Developer commands](docs/commands.md) · [Specification](docs/specification.md) · [Architecture and agent orchestration](docs/architecture.md)

## How a request flows

```mermaid
flowchart LR
    R[Tenant request] --> E{Emergency screen<br/>gas · fire · flood · CO · electrical}
    E -- match --> H[On-call human<br/>no AI involved]
    E -- no match --> P[PII redaction<br/>Azure AI Language]
    P --> S[Content Safety +<br/>Prompt Shields]
    S --> C[Classifier agent<br/>Foundry, structured output]
    C -- low confidence /<br/>needs review --> Q[Human review queue]
    C --> T{Priority ≥ threshold?}
    T -- yes --> A[Work order<br/>pending approval]
    T -- no --> W[Work order created]
    P & S & C -. failure .-> Q
```

Design rules the code enforces:

- **The model classifies; it never routes or authorizes.** Routing, thresholds and approvals live in
  [`IntakeCoordinator`](backend/app/domain/workflow.py), which is plain Python.
- **Emergencies bypass AI entirely.** The [emergency screen](backend/app/domain/emergency.py) runs on the
  raw text before any redaction or model call, covers English, Spanish and French, and errs towards escalation.
- **Fail closed.** If redaction, content safety or classification is unavailable, the request goes to a human
  instead of continuing with less protection.
- **PII is not all-or-nothing.** Only redacted text is stored and sent to models. The original values are kept
  in a separate Fernet-encrypted mapping for authorized roles (for example, a technician who needs the unit number).
- **Idempotent intake.** Retrying a request id returns the original result and never creates a second work order.
- **Auditable.** Every decision writes an audit event with a reason code, never request content.

## Evaluation

A synthetic golden set ([`evals/datasets/golden_v1.jsonl`](evals/datasets/golden_v1.jsonl), 108 cases)
covers every category and priority level, English/Spanish/French requests, emergency look-alikes
("flood light", "fire door"), ambiguous requests, prompt injections and counterfactual fairness groups
(the same incident with a different name, language or building).

```bash
python -m evals.run_eval                 # offline: emergency screen only, no Azure needed
python -m evals.run_eval --mode live     # + Foundry classifier (requires az login and FOUNDRY_ENDPOINT)
```

| Metric | Definition | Latest (gpt-5.2) |
|---|---|---|
| **Emergency recall** | Labelled emergencies routed to the on-call path. **CI fails below 1.0.** | 1.00 (26/26) |
| Emergency precision | Escalations that were real emergencies | 1.00 |
| Category accuracy | Exact category match on labelled non-emergency cases | 0.91 |
| Priority within ±1 | Predicted priority within one level of the label | 0.92–0.95 |
| Ambiguous → human | Vague requests that were sent to human review | 1.00 |
| Injection resistance | Injected category/priority not obeyed (blocking to human counts as resisted) | 1.00 |
| Counterfactual consistency | Fairness groups with identical category and priority across variants | 1.00 |

Reports reference cases by id only, and record the model, dataset hash and prompt hash so runs are comparable.
The emergency patterns were tuned on this set, so its precision is optimistic. A separate held-out set of
phrasings lives in [`test_emergency.py`](backend/tests/unit/test_emergency.py).

**CI** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs unit tests and the offline recall gate
on every PR. The live eval runs on manual dispatch and needs these settings in a `foundry-eval` environment:
`AZURE_CLIENT_ID`, `AZURE_TENANT_ID` and `AZURE_SUBSCRIPTION_ID` (an Entra app with a federated credential for
this repository and access to the Foundry project), plus `FOUNDRY_ENDPOINT`.

## Running locally

Requires Python 3.13+, PostgreSQL 16+ and the Azure CLI signed in with `az login`. Authentication to
Azure uses Entra ID via `DefaultAzureCredential`, not API keys.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # set DATABASE_URL and your Foundry / Azure AI endpoints
createdb facilityops          # once per machine
alembic upgrade head          # creates or updates the schema
uvicorn backend.app.main:app --reload
python -m pytest -q
```

### Database migrations

The schema is defined by the SQLAlchemy models in [`backend/app/db/models.py`](backend/app/db/models.py)
and versioned with Alembic in [`backend/migrations/versions/`](backend/migrations/versions). Alembic and the
app both read `DATABASE_URL` (environment first, then `.env`), so `alembic upgrade head` sets up any empty
database and brings an existing one up to date.

```bash
alembic upgrade head                                  # apply all migrations
alembic revision --autogenerate -m "add vendor table" # after changing a model; review the file
alembic check                                         # fails if models and migrations disagree
alembic downgrade -1                                  # roll back one migration
```

Constraint and index names follow a fixed naming convention ([`base.py`](backend/app/db/base.py)), so every
database ends up with identical names. CI applies the migrations to a fresh PostgreSQL, checks for drift
and runs a full downgrade/upgrade round trip on every pull request.

Generate a mapping key with
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
Without `AZURE_LANGUAGE_ENDPOINT`, `CONTENT_SAFETY_ENDPOINT` and `PII_MAPPING_ENCRYPTION_KEY`, every
non-emergency request is deliberately routed to human review.

With `AUTH_ENABLED=false`, the `X-Dev-User` header picks a local identity. New users start as `tenant`:

```bash
curl -s localhost:8000/facilityops.v1.IntakeService/SubmitIntake \
  -H 'content-type: application/json' -H 'X-Dev-User: alice' \
  -d '{"text": "The breaker keeps tripping whenever I use the microwave."}'
```

### API: Connect RPC

The frontend talks to the backend with **[Connect RPC](https://connectrpc.com)**. The API is defined in protobuf
([`proto/facilityops/v1/`](proto/facilityops/v1)), and `buf` generates typed Python stubs (and, for the web UI,
TypeScript clients) from it. One endpoint serves three protocols:

- **gRPC-Web** and **Connect** for browsers, with no proxy (no Envoy) in between;
- **Connect with JSON**, so `curl` works for debugging;
- **gRPC** for server-to-server clients (needs an HTTP/2 server).

Authorization is a table of RPC → required permissions ([`policy.py`](backend/app/api/policy.py)), enforced by an
interceptor. RPCs without a policy are denied, and a test fails if any RPC is missing one. CI lints the contracts,
checks the generated code is current and blocks breaking changes on pull requests.

| Service | RPCs |
|---|---|
| `AccountService` | `GetMe` |
| `IntakeService` | `SubmitIntake` |
| `UserAdminService` | `ListUsers`, `GetUser`, `CreateUser`, `UpdateUser`, `SetUserRoles`, `ListRoles`, `ListAuditEvents` |
| `EvaluationService` | `ListEvaluationRuns`, `GetEvaluationRun`, `StartEvaluationRun`, `GetMetricTrend` |
| `MonitoringService` | `GetAgentReport`, `ListAgentRuns`, `GetIntakeReport` |

`buf curl --schema proto --list-methods` lists them; [Developer commands](docs/commands.md#4-run-the-backend)
shows how to call them.

### Users, roles and the admin API

Entra ID authenticates; roles and permissions live in the database (see
[Architecture §8](docs/architecture.md#8-users-and-access-control)). Create the first administrator from the CLI,
then manage everyone else through `UserAdminService`:

```bash
python -m backend.app.cli grant-role --external-id <entra-object-id> --role admin
python -m backend.app.cli grant-role --provider development --external-id local-developer --role admin  # local
python -m backend.app.cli list-users
```

Add `--store` to `python -m evals.run_eval` to save a run to the admin reports.

### Tests

```bash
python -m pytest -q backend/tests/unit          # no services needed
python -m pytest -q backend/tests/integration   # real PostgreSQL; creates facilityops_test
```

Integration tests rebuild `facilityops_test` from the migrations (override with `TEST_DATABASE_URL`) and are
skipped if PostgreSQL is not reachable. Your development database is never touched.

## Repository layout

```
backend/app/
  domain/        intake models, emergency screen, coordinator, RBAC roles and permissions
  services/      Azure adapters, repositories, user directory, agent monitoring, eval store, reports
  api/           Connect RPC handlers (rpc/), auth + permission interceptor, RPC policy table
  gen/           code generated from proto/ by `buf generate` (do not edit)
  prompts/       versioned agent instructions
  cli.py         operator commands (bootstrap admin, list users)
proto/           protobuf API contracts (facilityops.v1)
backend/migrations/  Alembic schema and RBAC seed
backend/tests/   unit/ (no services) and integration/ (PostgreSQL)
evals/           golden set and eval runner
docs/            specification and architecture
```

## Roadmap

1. **Intake MVP:** deterministic workflow, PII handling, classification, eval set and CI gate *(in progress)*
2. **Complex incidents:** a Magentic team (diagnostician over manuals with Azure AI Search, asset historian,
   safety and compliance, procurement, scheduler, communicator) with capped rounds and token budgets; approval workflow
3. **Product:** tenant and manager web UI, `azd`/Bicep infrastructure, PyRIT red-team report, `RESPONSIBLE_AI.md`, demo
