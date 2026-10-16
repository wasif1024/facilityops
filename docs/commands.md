# Developer Commands

Every command needed to set up, run, test and evaluate FacilityOps AI locally.

> **Run every command from the project root**: the folder that contains `alembic.ini`, `requirements.txt`
> and `README.md`. Alembic, the app settings (`.env`) and the Python imports (`backend.app...`) all expect it.

Contents: [First-time setup](#1-first-time-setup) · [Daily start](#2-daily-start) ·
[Database and migrations](#3-database-and-migrations) · [Run the backend](#4-run-the-backend) ·
[API contracts (protobuf)](#5-api-contracts-protobuf) · [Users and roles](#6-users-and-roles) ·
[Tests](#7-tests) · [Evaluation](#8-evaluation) · [Troubleshooting](#9-troubleshooting)

## 1. First-time setup

Prerequisites: Python 3.13+, PostgreSQL 16+ (Homebrew or Postgres.app), Azure CLI.

```bash
# 1. Python environment and dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

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
| `PII_MAPPING_ENCRYPTION_KEY` | see below | Storing the PII mapping |

`.env` is gitignored. Never commit it.

```bash
# 3. Generate the PII mapping encryption key and paste it into .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 4. Sign in to Azure (the app uses Entra ID, not API keys)
az login

# 5. Create the database and apply all migrations
createdb facilityops
alembic upgrade head

# 6. Make yourself an admin for local development
python -m backend.app.cli grant-role --provider development --external-id local-developer --role admin
```

Without the three Azure AI settings the app still runs, but every non-emergency request is routed to human
review on purpose (fail closed).

## 2. Daily start

```bash
cd /path/to/facilityops-ai
source .venv/bin/activate
brew services start postgresql@18      # if PostgreSQL is not already running
alembic upgrade head                   # picks up any new migrations after a git pull
uvicorn backend.app.main:app --reload
```

## 3. Database and migrations

The schema lives in `backend/app/db/models.py` and is versioned by Alembic in `backend/migrations/versions/`.
Alembic reads `DATABASE_URL` from the environment first, then from `.env`.

| Task | Command |
|---|---|
| Apply all pending migrations | `alembic upgrade head` |
| Show the database's current migration | `alembic current` |
| List all migrations | `alembic history` |
| Check the models and migrations agree | `alembic check` |
| Undo the latest migration | `alembic downgrade -1` |
| Undo everything (empties the schema) | `alembic downgrade base` |
| Target another database once | `DATABASE_URL=postgresql+psycopg://<user>@localhost:5432/<db> alembic upgrade head` |

**Creating a migration after changing a model:**

```bash
alembic revision --autogenerate -m "add vendor table"
# Read the new file in backend/migrations/versions/ and fix anything autogenerate missed
alembic upgrade head
alembic check                          # should print "No new upgrade operations detected."
```

Roles and permissions are seed data. Change them only in a new migration, and keep
`SYSTEM_ROLE_PERMISSIONS` in `backend/app/domain/rbac.py` in sync (an integration test checks this).

**PostgreSQL service (Homebrew):**

```bash
brew services start postgresql@18
brew services stop postgresql@18
brew services list                     # see what is running
pg_isready -h localhost -p 5432        # is the server accepting connections?
psql -h localhost -d facilityops       # open a SQL shell
```

## 4. Run the backend

```bash
uvicorn backend.app.main:app --reload             # http://localhost:8000
uvicorn backend.app.main:app --reload --port 8001  # another port
```

The API is **Connect RPC**: every call is a `POST` to `/<package>.<Service>/<Method>`, for example
`/facilityops.v1.IntakeService/SubmitIntake`. The same endpoint accepts the Connect protocol (JSON or binary
protobuf), gRPC-Web (what browsers use) and gRPC. The contracts live in `proto/facilityops/v1/`. The only plain
HTTP endpoint is `GET /health/live`.

**Local identities.** With `AUTH_ENABLED=false` (the default in `.env.example`), the `X-Dev-User` header chooses
who you are. A new name is created as a `tenant` on first use; without the header you are `local-developer`.

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

`buf`, `protoc-gen-py` and `protoc-gen-connectrpc` come from `requirements.txt`, so they work once the virtualenv
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

# Write the full JSON report to a file (gitignored folder)
python -m evals.run_eval --mode live --report evals/reports/live.json
```

The command exits with code 1 when a gate fails (emergency recall below 1.0, or category accuracy below the given
threshold), which is what fails CI. An admin can also start a run with the
`EvaluationService/StartEvaluationRun` RPC.

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `No 'script_location' key found in configuration` | Alembic run outside the project root | `cd` to the folder with `alembic.ini` |
| `ModuleNotFoundError: No module named 'backend'` | Run outside the project root, or with `pytest` instead of `python -m pytest` | Run from the root with `python -m ...` |
| `connection refused` on port 5432 | PostgreSQL not running | `brew services start postgresql@18` |
| `database "facilityops" does not exist` | Database not created yet | `createdb facilityops` then `alembic upgrade head` |
| `unavailable: User directory is unavailable` | Migrations not applied, so the users tables are missing | `alembic upgrade head` |
| `permission_denied` on `SubmitIntake` | You are `local-developer` (an admin); admins cannot submit requests | Add `-H 'X-Dev-User: alice'` |
| `permission_denied` on an admin RPC | Your identity lacks the permission | `python -m backend.app.cli grant-role ... --role admin` |
| `404` on an RPC | Wrong path; it is case-sensitive and includes the package | `buf curl --schema proto --list-methods` |
| `415 Unsupported Media Type` | Missing `content-type: application/json` | Add the header |
| Native gRPC client (`--protocol grpc`, grpcurl) fails | gRPC needs HTTP/2; uvicorn serves HTTP/1.1 | Use the Connect or gRPC-Web protocol, as browsers do |
| `buf: command not found` | Virtualenv not active | `source .venv/bin/activate` |
| CI says generated code is out of date | A `.proto` changed without regenerating | `buf generate` and commit `backend/app/gen` |
| Every request ends in `human_review` with `pii_redaction_unavailable` | Azure AI Language endpoint or encryption key not set | Fill in the Azure settings in `.env` |
| Live eval fails with an auth error | Not signed in to Azure | `az login` |
| `failed_precondition: An evaluation run is already in progress` | Another run started in the last 30 minutes | Wait for it to finish |
