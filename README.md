<div align="center">

# FacilityOps AI

**Enterprise-grade, responsible AI for facility maintenance, on Microsoft Foundry and the Microsoft Agent Framework.**

[![CI](https://github.com/wasif1024/facilityops/actions/workflows/ci.yml/badge.svg)](https://github.com/wasif1024/facilityops/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-3776AB?logo=python&logoColor=white)
![Node 24](https://img.shields.io/badge/node-24-5FA04E?logo=nodedotjs&logoColor=white)
![Microsoft Foundry](https://img.shields.io/badge/Microsoft-Foundry-0078D4?logo=microsoft&logoColor=white)

[Getting started](docs/getting-started.md) · [Architecture](docs/architecture.md) · [Enterprise AI concepts](docs/enterprise-ai-concepts.md) · [Screenshots](docs/screenshots.md)

</div>

<p align="center">
  <img src="docs/images/demo.gif" width="900" alt="Demo, about 70 seconds: a tenant reports a dripping tap, which is redacted, checked and classified into a work order automatically; a facility manager opens an AI incident plan for a boiler fault where every claim cites a manual, procedure or record, and approval is blocked because the gas permit has expired; on the revised, approved plan the application has assigned the technician; the manager approves a notice for the tenant; the decision trail shows every check, model call and decision; the technician sees the assigned work order and the tenant reads the notice.">
</p>

<p align="center"><sub>A 70-second walkthrough of the running app (synthetic data). More in <a href="docs/screenshots.md">Screenshots</a>.</sub></p>

## Overview

Tenants report maintenance problems by text, photo, voice note or scanned document. A deterministic pipeline
screens, redacts, classifies and routes each request with AI, and keeps people in charge of anything dangerous or
expensive.

The project is a worked, open-source example of the practices around AI that real companies need: guardrails,
privacy, human oversight, evaluation, identity and access control. **New to AI?** Start with
[Enterprise AI concepts](docs/enterprise-ai-concepts.md), which explains each one in plain words. All data in the
repository is synthetic.

## Features

- **Multimodal intake:** text, photos (Azure AI Vision), voice notes (Azure AI Speech) and scanned documents
  (Azure AI Document Intelligence), in English, Spanish and French.
- **Translation for staff:** Spanish and French requests reach staff in English via Azure AI Translator,
  translated only after personal data is redacted, with the original one click away.
- **Emergency bypass:** gas, fire or flooding go straight to an on-call person by local rules, before any AI.
- **Guardrails:** Content Safety on text and images, Prompt Shields against direct and hidden (in-document)
  prompt injection, upload checks by file bytes, structured model output, fail closed to a person.
- **Privacy:** personal data redacted before any model, log or trace; originals encrypted and revealable only to
  authorized roles, with every reveal audited.
- **Human in the loop:** uncertain or flagged requests go to a review queue; high-priority work needs approval.
- **Identity and RBAC:** Microsoft Entra ID (staff) and Entra External ID (resident sign-up); permissions checked
  on every API call, deny by default; no API keys anywhere.
- **Evaluation:** a synthetic golden set, fairness and injection tests, and an emergency-recall gate in CI.
- **Transparency:** a decision trail for every request (each check, its result and version), what the model was
  sent and answered, append-only audit logs, and structured logs correlated with traces.
- **Observability:** OpenTelemetry to Application Insights and agent monitoring dashboards.

## Architecture

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/agent-orchestration-dark.gif">
  <img src="docs/images/agent-orchestration-light.gif" alt="Animated walkthrough of the agent orchestration in 18 steps: a request passes the emergency screen, file and image checks, PII redaction and safety checks in code; the classifier agent suggests a category and priority and code routes it; a high-priority equipment problem is escalated to a Magentic team, where the Incident Manager agent sends the asset historian, diagnostician, safety and compliance, procurement, scheduler and communicator agents in turn; the team produces a cited incident plan; a facility manager approves, revises or rejects it; only the application acts; every step is recorded.">
</picture>

**How to read it.** The animation follows one request through the system in 18 steps (about 70 seconds, then it
repeats); a [still version](docs/images/agent-orchestration-light.png) shows everything at once. Grey boxes are
code, purple boxes are AI agents, amber boxes are people.
- **Every request** goes through a fixed pipeline in code (`IntakeCoordinator`), where one AI agent, the classifier,
  only suggests a category and priority. Emergencies skip the AI entirely.
- **Escalated incidents** (high-priority heating, electrical or plumbing) go to a team of seven agents. The
  **Incident Manager** agent plans and decides which specialist works next; six specialists each have read-only tools
  or knowledge, and all of them run within fixed budgets.
- **No agent can act.** The team proposes a plan with citations; a facility manager approves, revises or rejects it,
  and only then does the application carry out the approved actions.

The request flow in more detail:

```mermaid
flowchart LR
    R[Request<br/>text · photo · voice · scan] --> E{Emergency<br/>screen}
    E -- emergency --> H[On-call person<br/>no AI]
    E -- with a file --> F[File checks · image safety<br/>read the file]
    F --> P
    E -- text --> P[PII redaction]
    P --> S[Content Safety +<br/>Prompt Shields]
    S --> C[Classifier agent]
    C --> W[Work order /<br/>approval]
    F & P & S & C -. failure or flagged .-> Q[Human review]
```

Details of the incident team (escalation, the event-driven worker, the seven agents, their read-only tools and
knowledge base, plans and the approval gate): [architecture and agent orchestration](docs/architecture.md).

## Tech stack

| Layer | Technology |
|---|---|
| AI | Microsoft Foundry, Microsoft Agent Framework, Azure AI Language, Content Safety, Vision, Speech, Document Intelligence, Translator |
| Backend | Python 3.13, FastAPI (ASGI), Connect RPC + protobuf, SQLAlchemy, Alembic, PostgreSQL |
| Frontend | React 19, TypeScript, Vite, Tailwind CSS, shadcn/ui, Connect-Web (gRPC-Web), TanStack Query |
| Identity | Microsoft Entra ID, Entra External ID, MSAL, managed identity |
| Operations | OpenTelemetry, Application Insights, Docker, GitHub Actions |

## Quick start

**Prerequisites:** Python 3.13+, PostgreSQL 16+, Node 24 and, for the AI steps, the Azure CLI.
[Install commands for macOS, Windows and Linux](docs/getting-started.md#what-to-install-first).

```bash
git clone https://github.com/wasif1024/facilityops.git && cd facilityops
cp .env.example .env                    # set DATABASE_URL; add Azure endpoints to enable the AI steps
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
scripts/db.sh                           # create the database, apply migrations, create demo accounts
scripts/dev.sh                          # API + incident worker + web app → open http://localhost:5173
```

Without Azure the app still runs; AI steps fail closed to the human-review queue. Docker, running each side
separately, Azure setup and troubleshooting: [Getting started](docs/getting-started.md).

### Demo accounts

The migrations create one demo account per role, so you can explore straight away. Locally there are no passwords:
the app opens on **Choose a demo account**.

| Account | Role |
|---|---|
| `local-developer` | Administrator |
| `alice` | Tenant (normal user) |
| `fm` | Facility manager |
| `tech` | Technician |

> [!WARNING]
> Demo accounts are for learning only and work only in local development mode. **Before using this project for
> real, remove them** with `scripts/db.sh remove-demo-accounts` (create your own administrator first). They are
> never created when `APP_ENV=production`. [Why and how](docs/getting-started.md#demo-accounts).

### Deploy to Azure

`azd up` creates the Azure resources from [`infra/`](infra) (Bicep) and deploys the API, the incident worker and the
web app to Container Apps. Every service is reached with one managed identity: PostgreSQL with Entra sign-in and no
password, Service Bus for events, Key Vault for secrets. It reuses your existing AI Services and AI Search resources.
It creates paid resources (roughly $60–75 a month before model usage): read [Deploying to Azure](docs/deployment.md)
first.

## Responsible AI

The project applies Microsoft's six Responsible AI principles:

| Principle | In this project |
|---|---|
| Fairness | Counterfactual tests: the same request with a different name, language or building must be routed the same |
| Reliability and safety | Emergency bypass, fail closed, human approval, emergency-recall gate on every pull request |
| Privacy and security | Redaction before AI, encrypted role-gated reveal, no stored media, no API keys, deny-by-default access |
| Inclusiveness | Three languages; report by voice or photo; works on a phone |
| Transparency | A "Why this decision" trail for every request, the exact model input and answer, published metrics and known limitations in the [transparency note](RESPONSIBLE_AI.md) |
| Accountability | People approve risky actions; every decision and data reveal is audited |

The [Responsible AI transparency note](RESPONSIBLE_AI.md) sets out what the AI does and doesn't do, how well it
works, its known limitations and how people stay in control.

## Evaluation

| Metric | Result (gpt-5.2) |
|---|---|
| Emergency recall (CI gate, must be 1.00) | 1.00 |
| Category accuracy | 0.91 |
| Prompt-injection resistance | 1.00 |
| Counterfactual fairness (routes) | 1.00 |
| Photo and scanned-document cases (live) | 6/6 |

108 synthetic golden cases plus a live media eval. Method, all metrics and how to run them:
[specification §10](docs/specification.md#10-evaluation-and-acceptance).

## Documentation

| Document | Contents |
|---|---|
| [Getting started](docs/getting-started.md) | Installation, running locally, demo accounts, Azure setup |
| [Enterprise AI concepts](docs/enterprise-ai-concepts.md) | Each enterprise and Responsible AI concept, explained for newcomers |
| [Architecture](docs/architecture.md) | System, orchestration tiers, multi-agent design, trust boundaries |
| [Specification](docs/specification.md) | Requirements, guardrails, privacy, evaluation targets |
| [Developer commands](docs/commands.md) | Every command: database, backend, frontend, tests, evals, troubleshooting |
| [Identity setup](docs/identity-setup.md) | Entra External ID for residents, workforce Entra ID for staff |
| [Deploying to Azure](docs/deployment.md) | `azd up`: what is created and why, roles, steps, cost, removal |
| [Responsible AI transparency note](RESPONSIBLE_AI.md) | Intended uses, safeguards, performance, known limitations |
| [Frontend specification](docs/frontend-specification.md) | Screens per role, API usage, accessibility |
| [Screenshots](docs/screenshots.md) | Every workspace: tenant, facility manager, technician, administrator |

## Project structure

```
backend/      Python API: domain logic, Azure adapters, RPC handlers, migrations, tests
frontend/     React web app
proto/        API contracts (protobuf)
evals/        golden set and evaluation runners
infra/        Azure infrastructure (Bicep) for azd, with its hooks
scripts/      db.sh (database), dev.sh (run locally), setup scripts
docs/         documentation and diagrams
```

## Ideas for further work

Everything above is built and tested. These are natural next steps; the first three are ready to run but use paid
Azure resources, so they are left to whoever deploys the project:

- **Deploy to Azure** with `azd up` ([deployment guide](docs/deployment.md)); the templates are validated, not yet
  deployed.
- **Run the live incident evaluation** (`python -m evals.run_incident_scenarios --mode live --store`): Foundry's
  Groundedness and Tool Call Accuracy evaluators on the real team, targets ≥ 0.90.
- **Red-team scan** with the Azure AI Red Teaming Agent (PyRIT), and publish the report.
- **Resume a paused incident run from a checkpoint**; today a revision starts a new run with the manager's feedback
  and the previous plan.

Known limitations are listed in the [transparency note](RESPONSIBLE_AI.md#known-limitations).

## Contributing

Issues and pull requests are welcome. Please run the tests (`python -m pytest -q backend/tests/unit`,
`cd frontend && npm run test`) and keep all data synthetic: no real names, addresses or documents.

## License

Released under the [MIT License](LICENSE). You are free to use, modify and distribute this project, including
commercially, provided the copyright notice is kept.
