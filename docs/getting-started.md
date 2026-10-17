# Getting started

Everything needed to run FacilityOps AI on your computer, step by step. The [README](../README.md#quick-start) has
the short version.

You run two things: the **backend** (Python API on http://localhost:8000) and the **frontend** (web app on
http://localhost:5173). Locally there are no passwords: the app opens on a **Choose a demo account** page (tenant,
facility manager, technician or administrator), and each account sees only what its role allows.

## What to install first

Install only the tools on your computer; every library (FastAPI, React…) is installed by the project in the steps
below.

| Tool | Needed for | macOS ([Homebrew](https://brew.sh)) | Windows (PowerShell) | Linux (Ubuntu/Debian) |
|---|---|---|---|---|
| **Git** | Getting the code | `brew install git` | `winget install -e --id Git.Git` | `sudo apt install git` |
| **Python 3.13+** | Backend | `brew install python@3.13` | `winget install -e --id Python.Python.3.13` | `sudo apt install python3.13 python3.13-venv` ¹ |
| **PostgreSQL 16+** | Database (Option B) | `brew install postgresql@18`<br/>`brew services start postgresql@18` | [Installer](https://www.postgresql.org/download/windows/) | `sudo apt install postgresql` |
| **nvm + Node 24** | Frontend | `brew install nvm` ², then `nvm install 24` | `winget install -e --id CoreyButler.NVMforWindows`, then `nvm install 24` and `nvm use 24` | [nvm install script](https://github.com/nvm-sh/nvm#installing-and-updating), then `nvm install 24` |
| **Azure CLI** | AI features (signing in to Azure) | `brew install azure-cli` | `winget install -e --id Microsoft.AzureCLI` | `curl -sL https://aka.ms/InstallAzureCLIDeb \| sudo bash` |
| **Docker Desktop** | Option A only | `brew install --cask docker` | `winget install -e --id Docker.DockerDesktop` | [Docker Engine](https://docs.docker.com/engine/install/) |

¹ On older Ubuntu releases Python 3.13 comes from the [deadsnakes PPA](https://launchpad.net/~deadsnakes/+archive/ubuntu/ppa).
² Follow the lines Homebrew prints to add nvm to your shell profile, then open a new terminal.

**Windows:** the scripts (`scripts/db.sh`) are bash. Use [WSL](https://learn.microsoft.com/windows/wsl/install)
(recommended: then follow the Linux column inside WSL) or Git Bash. In PowerShell, activate the virtual environment
with `.venv\Scripts\Activate.ps1` instead of `source .venv/bin/activate`.

**Check your setup:**

```bash
git --version          # any recent version
python3 --version      # 3.13 or newer
psql --version         # 16 or newer (Option B)
node --version         # v24.x (after nvm use)
az --version           # any recent version
docker --version       # Option A only
```

**Installed for you by the project** (no separate install needed):

| Command | Installs |
|---|---|
| `pip install -r requirements-dev.txt` | FastAPI, Uvicorn, Connect RPC, SQLAlchemy and Alembic (database), Microsoft Agent Framework, Azure AI SDKs (Foundry, Language, Content Safety), Azure Identity, OpenTelemetry, Pillow, pytest, and buf for code generation |
| `npm install` (in `frontend/`) | React, Vite, TypeScript, Tailwind CSS, shadcn/ui components, Connect-Web, TanStack Query, MSAL (Microsoft sign-in), Vitest |

**Azure (only for the AI steps).** Without Azure, the app still runs: requests that need AI go to the human-review
queue (fail closed). To enable the AI you need an Azure subscription with:

- a **Microsoft Foundry project** with a chat model deployment (the classifier), and
- an **Azure AI services** resource (Language, Content Safety, Vision, Speech and Document Intelligence all run
  on it),

and your account needs the **Cognitive Services User** role on the AI services resource and the **Azure AI User**
role on the Foundry project. Put the endpoints in `.env` (see `.env.example`) and run `az login`; no keys are used.

## Option A: backend in Docker (fastest)

Needs only [Docker Desktop](https://www.docker.com/products/docker-desktop/) and, for the web app, Node 24.

```bash
# Backend: PostgreSQL + database migrations (including the demo accounts) + API on http://localhost:8000
docker compose up --build -d
```

Then start the frontend with `scripts/dev.sh frontend` (Node 24 needed, see Option B). Without Azure settings, everything except the AI steps works, and
those fail closed to the human-review queue. To use your own Azure AI resources, copy `.env.docker.example` to
`.env.docker` ([details](commands.md#11-quick-start-with-docker)).

## Option B: everything on your machine

**Prerequisites:** Python 3.13+, PostgreSQL 16+, Node 24 and the Azure CLI ([what to install](#what-to-install-first)).

**1. Configure** (from the project root)

```bash
cp .env.example .env              # set DATABASE_URL; add your Azure AI endpoints to enable the AI steps
az login                          # Azure access uses your Entra ID sign-in, never API keys
```

**2. Install** (once)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
scripts/db.sh                     # creates the database, applies all migrations, creates the demo accounts
nvm install 24                    # Node for the frontend
```

**3. Run** with [`scripts/dev.sh`](../scripts/dev.sh)

```bash
scripts/dev.sh                    # backend + frontend together; open http://localhost:5173 (Ctrl+C stops both)
```

Or run each side in its own terminal:

```bash
scripts/dev.sh backend            # API on http://localhost:8000
scripts/dev.sh frontend           # web app on http://localhost:5173 (needs the backend running)
```

The script applies new migrations, installs frontend packages on the first run and creates
`frontend/.env.local` if missing. Add `--entra` to use Microsoft Entra sign-in instead of the demo accounts.

**4. Add demo data** (optional, terminal 3, with the backend running)

```bash
source .venv/bin/activate
python scripts/seed_demo.py       # synthetic requests, approvals and work orders, like the screenshots in the README
```

## Demo accounts

To make a fresh clone usable straight away, **the database migrations create four demo accounts**, one per role.
The web app opens on **Choose a demo account**; pick one. **Sign out** (top right) brings you back, and the switcher
next to it changes account directly.

<p align="center"><img src="images/dev-sign-in.png" alt="Choose a demo account: tenant, facility manager, technician, administrator" width="560"></p>

| Account | Role | Use it to try | Password |
|---|---|---|---|
| **`local-developer`** | **Administrator** | Users and roles, audit log, monitoring, evaluations | none |
| **`alice`** | **Tenant** (normal user) | Reporting problems by text, photo, voice or scanned document, and following them | none |
| `fm` | Facility manager | Review queue, approvals, assigning technicians, revealing personal details | none |
| `tech` | Technician | Work orders assigned to you | none |

Any other name you type starts as a new tenant.

**Why there are no passwords, and why this is safe locally.** These are *development identities*: the browser just
says who it is (`X-Dev-User`), so you can switch roles in a second while learning. The backend accepts them **only**
when `AUTH_ENABLED=false` and `APP_ENV` is not `production`. With real sign-in on, they can't be used at all.
Publishing shared passwords for real accounts in a public repository would be unsafe, so there are none, and in
Entra mode there are no shared test accounts: residents create their own account on Microsoft's sign-up page, and
the first administrator is granted from the CLI ([identity setup](identity-setup.md)).

> **Using this project for real? Remove the demo accounts.** They exist only for learning and demos. Create your own
> administrator first, then delete them:
>
> ```bash
> python -m backend.app.cli grant-role --external-id <your-entra-object-id> --role admin
> scripts/db.sh remove-demo-accounts      # refuses if no other administrator would remain (--force overrides)
> ```
>
> Production databases never get them: the migration skips them when `APP_ENV=production`, and
> `SEED_DEMO_ACCOUNTS=false` turns them off anywhere. `scripts/db.sh add-demo-accounts` brings them back. Creating
> and removing them is recorded in the audit log.

**Role-based access control (RBAC) is enforced twice.** The menu shows only the pages your permissions allow, and
opening another page's address shows "You don't have access". More importantly the backend checks permissions on
every API call (deny by default, [`policy.py`](../backend/app/api/policy.py)), so even a hand-made request is refused:

```bash
curl -s localhost:8000/facilityops.v1.UserAdminService/ListUsers \
  -H 'content-type: application/json' -H 'X-Dev-User: alice' -d '{}'
# {"code": "permission_denied", "message": "Insufficient permissions"}
```

## Try the AI incident team

Needs `FOUNDRY_ENDPOINT` in `.env`, the synthetic facility data (`scripts/db.sh seed-world`) and the worker, which
`scripts/dev.sh` starts for you.

1. As **`alice`**, report: *"The boiler HX-200 in plant room B at Harbor View shows error E04 again and the radiators
   are cold."* It is classified as heating, priority 4, so a work order waits for approval and the request goes to
   the incident team. Alice sees **Being investigated**.
2. As **`fm`**, open the request (or its work order). The **Incident team** panel shows the specialists working,
   then the plan after a minute or two: the evidence with citations, the flags and the proposed actions.
3. This boiler's gas work permit has expired in the synthetic data, so the plan is **safety-blocked** and can't be
   approved. That is the system working: no agent and no person can approve unsafe work.
4. Use **Ask for a revision** or **Reject**. To see an approval go through, report a problem whose plan isn't
   blocked, or send any other request to the team with **Send to incident team**.
5. After **Approve plan**, the application (not an agent) opens the work order, assigns the named technician (who
   then sees it as **`tech`**), raises purchase and visit requests and records lockout. Each action shows its result
   in the panel and as a step in the **Decision trail**.
6. Still in the panel, **Notice to the tenant** holds the AI communicator's English draft. Edit it if you like and
   choose **Approve and send**. If the tenant wrote in French or Spanish, the application translates it; as
   **`alice`**, open the request to read it under **Messages from facility management**.

## Run the tests

```bash
python -m pytest -q backend/tests/unit          # backend, no Azure needed
python -m evals.run_eval                        # emergency-recall gate, no Azure needed
cd frontend && npm run test                     # frontend
```

All commands, including troubleshooting: [docs/commands.md](commands.md).

## Production sign-in

Locally you choose a demo account; in production, residents sign up and sign in with **Microsoft Entra External
ID** and staff use their **work account** (workforce Entra ID). Passwords, email verification and password reset
stay with Microsoft. See [Identity setup](identity-setup.md).

<p align="center"><img src="images/sign-in.png" alt="Sign-in page: residents and staff" width="560"></p>


## Configuration notes

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
