# Developer Commands

Every command needed to set up, run, test and evaluate FacilityOps AI locally.

> **Run every command from the project root**: the folder that contains `requirements.txt` and `README.md`. The
> app settings (`.env`) and the Python imports (`backend.app...`) expect it. (`scripts/db.sh` works from anywhere.)

Contents: [First-time setup](#1-first-time-setup) · [Daily start](#2-daily-start) ·
[Database and migrations](#3-database-and-migrations) · [Run the backend](#4-run-the-backend) ·
[Run the frontend](#41-run-the-frontend) · [API contracts (protobuf)](#5-api-contracts-protobuf) ·
[Users and roles](#6-users-and-roles) · [Tests](#7-tests) · [Evaluation](#8-evaluation) ·
[Troubleshooting](#9-troubleshooting)

## 1. First-time setup

Two ways to run the backend: **Docker** (quickest; nothing else to install) or **native** (Python and PostgreSQL
on your machine; needed to develop and run the tests).

### 1.1 Quick start with Docker

Prerequisite: [Docker Desktop](https://www.docker.com/products/docker-desktop/).

```bash
docker compose up --build -d                  # PostgreSQL + migrations + API on http://localhost:8000
docker compose ps                             # api "healthy", migrate "Exited (0)"

# The migrate step also creates the demo accounts (local-developer is an admin); see section 6.1

# Try it
curl -s localhost:8000/facilityops.v1.IntakeService/SubmitIntake \
  -H 'content-type: application/json' -H 'X-Dev-User: alice' \
  -d '{"text": "There is a strong gas smell in the hallway"}'
```

| Command | What it does |
|---|---|
| `docker compose up --build -d` | Build the image and start everything in the background |
| `docker compose logs -f api` | Follow the API logs |
| `docker compose exec api python -m backend.app.cli list-users` | Run any CLI command inside the container |
| `docker compose exec postgres psql -U facilityops` | SQL shell on the container database |
| `docker compose --profile tools up -d` | Also start pgAdmin on http://localhost:5050 (`admin@example.com` / `local-dev-only`) |
| `docker compose up --build -d` (again) | After `git pull`: rebuilds the image and re-runs the migrations before the API restarts |
| `docker compose down` | Stop; the database is kept |
| `docker compose down -v` | Stop and delete the database |

What runs (`compose.yaml`): `postgres` (PostgreSQL 18, on host port **5433** so it doesn't clash with a local
PostgreSQL on 5432), `migrate` (applies the migrations once, then exits; the API waits for it to succeed) and
`api` (the [`Dockerfile`](../Dockerfile) image, non-root, with a health check). Ports bind to `127.0.0.1` only.
Override them with `API_PORT`, `POSTGRES_PORT` or `PGADMIN_PORT`, for example `API_PORT=8080 docker compose up -d`.

**Without Azure** everything except the AI steps works: emergency screening, users and roles, the review queue,
work orders, admin reports and offline evals. Non-emergency requests go to human review with
`pii_redaction_unavailable`, the fail-closed behaviour. **With Azure**, copy `.env.docker.example` to
`.env.docker` (gitignored) and fill in your own endpoints and a service principal: a container can't use your
`az login`. When deployed to Azure, a managed or workload identity replaces the secret.

### 1.2 Native setup

Prerequisites: Python 3.13+, PostgreSQL 16+ (Homebrew or Postgres.app), Azure CLI. Install commands for macOS,
Windows and Linux: [README, What to install first](../README.md#what-to-install-first).

```bash
# 1. Python environment and dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

# 2. Configuration: copy the template, then edit .env
cp .env.example .env
```

Set at least these in `.env`:

| Variable | Example | Needed for |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://<user>@localhost:5432/facilityops` | Everything |
| `FOUNDRY_ENDPOINT` | `https://<resource>.services.ai.azure.com/api/projects/<project>` | Classifier, live eval |
| `FOUNDRY_MODEL` | `gpt-5.2` | Classifier, live eval |
| `AZURE_LANGUAGE_ENDPOINT` | your Azure AI Language endpoint | PII redaction |
| `CONTENT_SAFETY_ENDPOINT` | your Azure AI Content Safety endpoint | Content Safety, Prompt Shields |
| `VISION_ENDPOINT` | your Azure AI Vision endpoint | Photo intake (caption + OCR) |
| `SPEECH_ENDPOINT` | your Azure AI Speech endpoint | Voice intake (transcription) |
| `DOCUMENT_INTELLIGENCE_ENDPOINT` | your Azure AI Document Intelligence endpoint (an AI Services resource works) | Scanned-document intake (read model) |
| `PII_MAPPING_ENCRYPTION_KEY` | see below | Storing and revealing the PII mapping |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | from your Application Insights resource | OpenTelemetry export (optional) |

One multi-service **AIServices** resource (`https://<name>.cognitiveservices.azure.com/`), such as the one behind
a Foundry project, covers Language, Content Safety, Vision and Speech, so all four can use the same endpoint.
Your Azure account needs a data-plane role on it (for example *Foundry User* / *Cognitive Services User*).
Keep the PII key safe: losing it makes existing mappings unreadable.

`.env` is gitignored. Never commit it.

```bash
# 3. Generate the PII mapping encryption key and paste it into .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 4. Sign in to Azure (the app uses Entra ID, not API keys)
az login

# 5. Create the database and apply all migrations
scripts/db.sh create
scripts/db.sh up
# The migrations also create the demo accounts, including the local admin (section 6.1)
```

Without the Azure AI settings the app still runs, but every non-emergency request is routed to human review on
purpose (fail closed).

## 2. Daily start

`scripts/dev.sh` starts the project. It applies any new migrations first, installs frontend packages on the first run,
creates `frontend/.env.local` if it's missing, and stops with a clear message if a port is already in use.

```bash
brew services start postgresql@18      # if PostgreSQL is not already running

scripts/dev.sh                         # backend + frontend in one terminal (Ctrl+C stops both)
scripts/dev.sh backend                 # backend only:  http://localhost:8000
scripts/dev.sh frontend                # frontend only: http://localhost:5173 (start the backend first)

scripts/dev.sh --entra                 # both, with Microsoft Entra sign-in (docs/identity-setup.md)
scripts/dev.sh backend --entra         # either side alone, with Entra sign-in
API_PORT=8001 WEB_PORT=5174 scripts/dev.sh   # other ports
scripts/dev.sh help                    # summary
```

In the combined mode every line is labelled `[api]` or `[web]`; if either side stops, the other is stopped too.
Run the two sides in separate terminals when you want their logs apart. The script works from any folder and
needs no activated virtual environment.

## 3. Database and migrations

The schema lives in `backend/app/db/models.py` and is versioned in `backend/migrations/versions/`. Every database
task goes through `scripts/db.sh`, which reads `DATABASE_URL` from the environment first, then from `.env`.

### 3.1 Migration script: `scripts/db.sh`

Safe defaults, works from any folder. Run `scripts/db.sh help` for the built-in summary.

```bash
# Setup or update (the usual command): create the database if needed, then apply all migrations
scripts/db.sh

# Setup
scripts/db.sh create                          # create the database in DATABASE_URL if it does not exist

# Apply
scripts/db.sh up                              # apply all pending migrations
scripts/db.sh up 0002_users_rbac_monitoring   # apply up to a specific revision only

# Inspect
scripts/db.sh status                          # current revision, latest revision, pending or up to date
scripts/db.sh history                         # all migrations, with the current one marked
scripts/db.sh check                           # fail if models and migrations disagree
scripts/db.sh sql                             # print the SQL for all migrations instead of running it
scripts/db.sh sql 0002_users_rbac_monitoring:head   # SQL for a range only

# Roll back (asks you to type the database name first)
scripts/db.sh down                            # undo the latest migration
scripts/db.sh down 2                          # undo the latest two
scripts/db.sh down 0001_phase1_schema         # go back to a specific revision
scripts/db.sh down base                       # undo every migration (empty schema, all data lost)
scripts/db.sh reset                           # undo everything, then re-apply (fresh empty schema)

# Create
scripts/db.sh new "add vendor table"          # new migration from model changes -> 0005_add_vendor_table.py

# Demo accounts (section 6.1)
scripts/db.sh remove-demo-accounts            # before using the project for real
scripts/db.sh add-demo-accounts               # bring them back locally

# Options
scripts/db.sh down --yes                      # skip the confirmation (scripts and CI only)
DATABASE_URL=postgresql+psycopg://<user>@localhost:5432/<db> scripts/db.sh status   # another database, once
```

Safety: `down` and `reset` ask you to type the database name, refuse to run when `APP_ENV=production`, and stop
(instead of assuming yes) when there is no terminal unless `--yes` is given. Printed URLs hide passwords.

### 3.2 Common workflows

**New machine or fresh clone**

```bash
scripts/db.sh                                 # creates the database, applies everything, ends with "up to date"
```

**After `git pull`** (someone added migrations)

```bash
scripts/db.sh                                 # applies only what is new
```

**Changing the schema**

```bash
# 1. Edit backend/app/db/models.py
scripts/db.sh up                              # the database must be current before generating
scripts/db.sh new "add vendor table"          # writes backend/migrations/versions/0004_add_vendor_table.py
# 2. Read the new file; autogenerate misses renames, data migrations and seed data
scripts/db.sh up
scripts/db.sh check                           # expect "No new upgrade operations detected."
scripts/db.sh down && scripts/db.sh up        # prove the downgrade works too
```

**Undo a migration you created but have not committed**

```bash
scripts/db.sh down                            # roll the database back first
rm backend/migrations/versions/0004_add_vendor_table.py
```

Never delete a migration that is already committed or applied elsewhere; write a new migration that reverses it.

**Start over with an empty local database**

```bash
scripts/db.sh reset                           # all data is lost; the demo accounts are created again
```

Roles and permissions are seed data. Change them only in a new migration, and keep
`SYSTEM_ROLE_PERMISSIONS` in `backend/app/domain/rbac.py` in sync (an integration test checks this).

### 3.4 PostgreSQL service (Homebrew)

```bash
brew services start postgresql@18
brew services stop postgresql@18
brew services list                     # see what is running
pg_isready -h localhost -p 5432        # is the server accepting connections?
psql -h localhost -d facilityops       # open a SQL shell
```

## 4. Run the backend

```bash
scripts/dev.sh backend                 # migrations, then http://localhost:8000 with auto-reload
API_PORT=8001 scripts/dev.sh backend   # another port
```

Under the hood this runs `uvicorn backend.app.main:create_app --factory --reload`; the app is built by a factory,
so there is no module-level `app` to point uvicorn at.

The API is **Connect RPC**: every call is a `POST` to `/<package>.<Service>/<Method>`, for example
`/facilityops.v1.IntakeService/SubmitIntake`. The same endpoint accepts the Connect protocol (JSON or binary
protobuf), gRPC-Web (what browsers use) and gRPC. The contracts live in `proto/facilityops/v1/`. The only plain
HTTP endpoint is `GET /health/live`.

**Local identities.** With `AUTH_ENABLED=false` (the default in `.env.example`), the `X-Dev-User` header chooses
who you are. A new name is created as a `tenant` on first use. For `curl` convenience, a call without the header
is treated as `local-developer`; the web app never relies on this (it always sends the chosen account).

**Calling RPCs with curl** (Connect protocol, JSON). Field names are camelCase, enums are their full names
(`"USER_STATUS_DISABLED"`), and 64-bit integers come back as strings:

```bash
# Who am I, and what can I do?
curl -s localhost:8000/facilityops.v1.AccountService/GetMe \
  -H 'content-type: application/json' -H 'X-Dev-User: alice' -d '{}'

# Submit a maintenance request as a tenant
curl -s localhost:8000/facilityops.v1.IntakeService/SubmitIntake \
  -H 'content-type: application/json' -H 'X-Dev-User: alice' \
  -d '{"text": "The breaker keeps tripping whenever I use the microwave."}'

# A photo or voice note (base64 in JSON; the web UI sends binary protobuf). Optional caption and language hint.
curl -s localhost:8000/facilityops.v1.IntakeService/SubmitIntake \
  -H 'content-type: application/json' -H 'X-Dev-User: alice' \
  -d "{\"source\": \"REQUEST_SOURCE_PHOTO\", \"mediaType\": \"image/jpeg\", \"media\": \"$(base64 -i leak.jpg)\"}"
curl -s localhost:8000/facilityops.v1.IntakeService/SubmitIntake \
  -H 'content-type: application/json' -H 'X-Dev-User: alice' \
  -d "{\"source\": \"REQUEST_SOURCE_VOICE\", \"mediaType\": \"audio/wav\", \"language\": \"es\", \"media\": \"$(base64 -i note.wav)\"}"
# A scanned document: PDF or TIFF (first 5 pages are read), or a photo of a page as JPEG/PNG
curl -s localhost:8000/facilityops.v1.IntakeService/SubmitIntake \
  -H 'content-type: application/json' -H 'X-Dev-User: alice' \
  -d "{\"source\": \"REQUEST_SOURCE_DOCUMENT\", \"mediaType\": \"application/pdf\", \"media\": \"$(base64 -i notice.pdf)\"}"

# My requests and their work-order status
curl -s localhost:8000/facilityops.v1.IntakeService/ListMyRequests \
  -H 'content-type: application/json' -H 'X-Dev-User: alice' -d '{}'

# Facility manager: review queue, decisions, PII reveal (make "fm" a facility_manager first, see section 6)
FM=(-H 'content-type: application/json' -H 'X-Dev-User: fm')
curl -s localhost:8000/facilityops.v1.ReviewQueueService/ListReviewCases "${FM[@]}" -d '{}'
curl -s localhost:8000/facilityops.v1.ReviewQueueService/ResolveReviewCase "${FM[@]}" \
  -d '{"caseId": "<id>", "resolution": "REVIEW_RESOLUTION_WORK_ORDER_CREATED", "category": "MAINTENANCE_CATEGORY_PLUMBING", "priority": 2}'
curl -s localhost:8000/facilityops.v1.WorkOrderService/ApproveWorkOrder "${FM[@]}" -d '{"workOrderId": "<id>"}'
curl -s localhost:8000/facilityops.v1.WorkOrderService/AssignWorkOrder "${FM[@]}" \
  -d '{"workOrderId": "<id>", "technicianUserId": "<user-id>"}'
curl -s localhost:8000/facilityops.v1.IntakeService/RevealRequestDetails "${FM[@]}" -d '{"requestId": "<id>"}'

# Admin reports (as local-developer, after step 6 of the setup)
curl -s localhost:8000/facilityops.v1.UserAdminService/ListUsers -H 'content-type: application/json' -d '{}'
curl -s localhost:8000/facilityops.v1.MonitoringService/GetAgentReport -H 'content-type: application/json' -d '{"days": 7}'
curl -s localhost:8000/facilityops.v1.MonitoringService/GetIntakeReport -H 'content-type: application/json' -d '{"days": 7}'
curl -s localhost:8000/facilityops.v1.EvaluationService/ListEvaluationRuns -H 'content-type: application/json' -d '{}'
```

**Calling RPCs with `buf curl`**, which reads the `.proto` files, so it validates field names and pretty-prints:

```bash
buf curl --schema proto --list-methods                                   # every RPC
buf curl --schema proto -H 'X-Dev-User: alice' --data '{}' \
  http://localhost:8000/facilityops.v1.AccountService/GetMe
buf curl --schema proto --protocol grpcweb --data '{"days": 7}' \
  http://localhost:8000/facilityops.v1.MonitoringService/GetIntakeReport  # as a browser would
```

Errors come back as `{"code": "permission_denied", "message": "..."}` with a matching HTTP status
(`unauthenticated` 401, `permission_denied` 403, `not_found` 404, `invalid_argument` 400, `resource_exhausted` 429).

### 4.1 Run the frontend

The web app lives in [`frontend/`](../frontend) (React, TypeScript, Vite, Tailwind, shadcn/ui) and needs
**Node 24**. With the backend running on port 8000:

```bash
nvm install 24                   # once; frontend/.nvmrc pins Node 24
scripts/dev.sh frontend          # http://localhost:5173
```

The script selects Node 24, runs `npm install` on the first run, creates `frontend/.env.local` from
`.env.example` if it's missing (the defaults work locally) and starts the Vite dev server.

The app opens on **Choose a demo account**; Sign out returns to it. The accounts (`alice` tenant, `fm` facility
manager, `tech` technician, `local-developer` admin) are created by the migrations; see [section 6.1](#61-demo-accounts).

**Demo data:** with the backend running, `python scripts/seed_demo.py` drives the API as tenants, a facility
manager and technicians (synthetic requests in three languages, an emergency, approvals, assignments, a completed
job). Safe to re-run.

| Command (in `frontend/`) | What it does |
|---|---|
| `npm run dev` | Dev server with hot reload |
| `npm run test` | Unit and component tests (in-memory Connect backend) |
| `npm run lint` · `npm run typecheck` | oxlint · TypeScript |
| `npm run build` | Production build into `frontend/dist` |
| `npm run generate` | Regenerate the TypeScript client after a `.proto` change (run `buf generate` at the root for Python) |

## 5. API contracts (protobuf)

The `.proto` files in `proto/facilityops/v1/` are the API. Python messages and service stubs are generated into
`backend/app/gen/` and committed. CI fails if they are out of date, if lint fails, or if a pull request breaks
the contract for existing clients.

```bash
buf lint                                          # style and naming rules
buf generate                                      # regenerate backend/app/gen after editing a .proto
buf breaking --against '.git#branch=main'         # would this change break existing clients?
buf format -w                                     # format the .proto files
```

`buf`, `protoc-gen-py` and `protoc-gen-connectrpc` come from `requirements-dev.txt`, so they work once the virtualenv
is active. Never edit files in `backend/app/gen/` by hand.

**Adding an RPC:**

1. Add the `rpc` and its request/response messages to the right `.proto` file.
2. `buf lint && buf generate`
3. Implement the method on the matching class in `backend/app/api/rpc/`.
4. Add the RPC and its required permissions to `backend/app/api/policy.py`. RPCs without a policy are denied,
   and `test_every_rpc_has_an_access_policy` fails until you do.
5. If it is a new service, mount it in `backend/app/main.py`.

## 6. Users and roles

Roles: `tenant`, `technician`, `facility_manager`, `admin`. The CLI is for bootstrapping; day-to-day changes go
through the admin API so they are audited with the acting admin's id.

```bash
# Give a role (creates the user if needed)
python -m backend.app.cli grant-role --external-id <entra-object-id> --role admin
python -m backend.app.cli grant-role --provider development --external-id fm --role facility_manager

# List users and their roles
python -m backend.app.cli list-users
```

Through the API, as an admin:

```bash
# Set a user's roles (replaces the existing set)
curl -s localhost:8000/facilityops.v1.UserAdminService/SetUserRoles -H 'content-type: application/json' \
  -d '{"userId": "<user-id>", "roles": ["technician"]}'

# Disable a user
curl -s localhost:8000/facilityops.v1.UserAdminService/UpdateUser -H 'content-type: application/json' \
  -d '{"userId": "<user-id>", "status": "USER_STATUS_DISABLED"}'
```

### 6.1 Demo accounts

Migration `0004_demo_accounts` creates one account per role so a fresh clone can be explored at once:

| Account | Role |
|---|---|
| `local-developer` | admin |
| `alice` | tenant |
| `fm` | facility_manager |
| `tech` | technician |

They are development identities without passwords, accepted only when `AUTH_ENABLED=false` and `APP_ENV` is not
`production`. They exist for learning and demos; **remove them before using the project for real**:

```bash
python -m backend.app.cli grant-role --external-id <your-entra-object-id> --role admin   # your own admin first
scripts/db.sh remove-demo-accounts           # refuses if no other admin would remain
scripts/db.sh remove-demo-accounts --force   # remove anyway
scripts/db.sh add-demo-accounts              # create them again (not in production)
SEED_DEMO_ACCOUNTS=false scripts/db.sh       # migrate a new database without them
```

With `APP_ENV=production` the migration never creates them. Their requests and work orders stay when they are removed
(without an author), and each creation and removal is written to the security audit log.

## 7. Tests

```bash
python -m pytest -q backend/tests/unit           # fast, no services needed
python -m pytest -q backend/tests/integration    # needs PostgreSQL
python -m pytest -q                              # everything
python -m pytest -q -k emergency                 # only tests whose name matches
```

Integration tests create and rebuild a separate `facilityops_test` database from the migrations on every run.
Your `facilityops` database is never touched. Use another server or database with
`TEST_DATABASE_URL=postgresql+psycopg://<user>@<host>:5432/<db>`. If PostgreSQL is not reachable, the integration
tests are skipped, not failed.

## 8. Evaluation

```bash
# Offline: emergency screen only. No Azure needed. This is the CI gate.
python -m evals.run_eval

# Live: also runs the Foundry classifier on ~80 cases (needs az login and FOUNDRY_ENDPOINT; ~1-2 min)
python -m evals.run_eval --mode live --min-category-accuracy 0.85

# Save the run so it appears in the admin reports (works with either mode)
python -m evals.run_eval --mode live --store

# Consistency: classify every case N times and report how often answers and routes agree.
# --min-route-fairness 1.0 is the Phase 1 gate (CI's live eval runs exactly this).
python -m evals.run_eval --mode live --samples 5 --concurrency 8 --store \
  --min-category-accuracy 0.85 --min-route-fairness 1.0

# Write the full JSON report to a file (gitignored folder)
python -m evals.run_eval --mode live --report evals/reports/live.json

# Photo and scanned-document intake: synthetic files drawn in code, sent through the real services
# (Vision, Document Intelligence, Content Safety image + Prompt Shields, Language, classifier). ~1 min.
python -m evals.run_media_eval --report evals/reports/media.json
```

With `--samples N` the first sample feeds the usual metrics (so runs stay comparable), and four more are added:
`classification_self_agreement` (same category and priority every time), `route_self_agreement` (same path:
work order, pending approval or human review), and modal-answer fairness for labels and routes. The modal
versions tell bias (a group member differs systematically) apart from sampling noise (one unlucky draw).

The command exits with code 1 when a gate fails (emergency recall below 1.0, or category accuracy below the given
threshold), which is what fails CI. An admin can also start a run with the
`EvaluationService/StartEvaluationRun` RPC.

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: No module named 'backend'` | Run outside the project root, or with `pytest` instead of `python -m pytest` | Run from the root with `python -m ...` |
| `connection refused` on port 5432 | PostgreSQL not running | `brew services start postgresql@18` |
| `database "facilityops" does not exist` | Database not created yet | `scripts/db.sh` |
| `unavailable: User directory is unavailable` | Migrations not applied, so the users tables are missing | `scripts/db.sh` |
| `permission_denied` on `SubmitIntake` | You are `local-developer` (an admin); admins cannot submit requests | Add `-H 'X-Dev-User: alice'` |
| `permission_denied` on an admin RPC | Your identity lacks the permission | `python -m backend.app.cli grant-role ... --role admin` |
| `404` on an RPC | Wrong path; it is case-sensitive and includes the package | `buf curl --schema proto --list-methods` |
| `415 Unsupported Media Type` | Missing `content-type: application/json` | Add the header |
| Native gRPC client (`--protocol grpc`, grpcurl) fails | gRPC needs HTTP/2; uvicorn serves HTTP/1.1 | Use the Connect or gRPC-Web protocol, as browsers do |
| `buf: command not found` | Virtualenv not active | `source .venv/bin/activate` |
| Frontend: "Something went wrong on our side" on load | Backend not running on 8000 | Start it, then click Try again |
| Frontend: `npm install` or `npm run test` fails with an engine error | Node older than 24 | `nvm use` in `frontend/` |
| Frontend shows "You don't have access" | Your identity lacks the permission | Switch identity, or grant the role with the CLI |
| `docker compose up`: port is already allocated | Something else uses 8000 or 5433 | `API_PORT=8080 POSTGRES_PORT=5434 docker compose up -d` |
| `docker-credential-desktop: executable file not found` | Docker Desktop's tools are not on `PATH` in this shell | Open a new terminal after starting Docker Desktop, or add `/Applications/Docker.app/Contents/Resources/bin` to `PATH` |
| API container keeps restarting | Migration failed, or a bad setting in `.env.docker` | `docker compose logs migrate api` |
| CI says generated code is out of date | A `.proto` changed without regenerating | `buf generate` and commit `backend/app/gen` |
| Every request ends in `human_review` with `pii_redaction_unavailable` | Azure AI Language endpoint or encryption key not set | Fill in the Azure settings in `.env` |
| Live eval fails with an auth error | Not signed in to Azure | `az login` |
| `failed_precondition: An evaluation run is already in progress` | Another run started in the last 30 minutes | Wait for it to finish |
