# Deploying to Azure

FacilityOps AI deploys with the **Azure Developer CLI** (`azd`): one command creates the Azure resources from the
Bicep templates in [`infra/`](../infra) and deploys the three apps into them. This page explains what gets created,
why, what it costs, and the steps.

> **This creates paid resources.** Read [Cost](#cost) first. Running locally ([getting started](getting-started.md))
> needs none of this.

## What gets created, and why

Everything new goes into one resource group, `rg-<environment>`. The AI resources you already use (the AI Services
resource with your model deployments, and the AI Search service behind the knowledge base) are **reused**: the
template only grants the app's identity permission to use them. It never creates, changes or deletes them.

| Resource | What it does here | Enterprise practice it shows |
|---|---|---|
| **User-assigned managed identity** | The single identity all three apps run as | No keys or passwords anywhere: every service below is reached with Microsoft Entra ID |
| **Container Apps** (API, worker, web) | Runs the code. The API and the web app scale to zero when idle; the worker always runs one copy | Serverless containers; images pulled with the identity (no registry password) |
| **Container Registry** | Stores the app images, built in Azure by `azd` (no local Docker needed) | Admin user off; `AcrPull` role only |
| **PostgreSQL flexible server** | The database | **Entra-only sign-in**: password authentication is disabled, the app signs in with a token for its identity |
| **Service Bus** (topic `facilityops-events`) | Carries events between the API and the incident worker, one subscription per consumer | Local (shared-key) auth off; 3 deliveries then a dead-letter queue, which hands the incident to a person |
| **Key Vault** | Holds the PII-mapping encryption key and the rate-limit secret | RBAC mode; apps read secrets by reference, they are never in app settings or code |
| **Log Analytics + Application Insights** | Container logs and OpenTelemetry traces | Ingestion with Entra only (Monitoring Metrics Publisher role); traces carry ids and codes, never personal data |

Role assignments, all least-privilege and granted to the managed identity only:

| On | Role | For |
|---|---|---|
| Container Registry | AcrPull | Pull images |
| Key Vault | Key Vault Secrets User | Read the two secrets |
| Application Insights | Monitoring Metrics Publisher | Send traces (key-based ingestion is off) |
| Service Bus namespace | Azure Service Bus Data Sender, Data Receiver | Publish and receive events |
| Existing AI Services resource | Cognitive Services User, Foundry User | Language (PII), Content Safety, Vision, Speech, Document Intelligence, Translator; models through Foundry |
| Existing AI Search service | Search Index Data Reader | Read the knowledge base (read-only) |
| PostgreSQL | Entra administrator | Run migrations and the app |

**A trade-off to know about.** The app's identity is also the database's Entra administrator, because it runs the
migrations when the API starts. A stricter setup gives migrations their own identity and the app a role with only
data permissions; that is a sensible next step for a real deployment.

**Networking.** Services are reached over public endpoints with TLS and Entra ID, and PostgreSQL accepts connections
only from Azure services. Private endpoints and a virtual network are the next hardening step and are not part of
this template.

## Prerequisites

- An Azure subscription where you can create resources **and assign roles** (Owner, or Contributor plus User Access
  Administrator), including on the resource group of your existing AI resources.
- The existing AI Services resource with the model deployments the app uses (`gpt-5.2`, `gpt-5.4-mini`) and,
  optionally, the AI Search service with the knowledge base (`scripts/knowledge.sh setup`).
- Sign-in set up as in [identity setup](identity-setup.md): the staff (workforce) app registrations, and optionally the
  resident (External ID) ones.
- [Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd), the Azure CLI,
  and Python 3 (the setup hook uses it to generate secrets). Docker is **not** needed: images are built in Azure.

## Steps

**1. Sign in and create an environment.** An environment is a named deployment (for example `facilityops-dev`); its
settings are kept in `.azure/<name>/`, which is gitignored.

```bash
azd auth login
azd env new facilityops-dev
azd env set AZURE_LOCATION eastus
```

**2. Point it at your existing AI resources and sign-in settings.**

```bash
azd env set AZURE_AI_SERVICES_NAME <your-ai-services-resource>
azd env set AZURE_AI_RESOURCE_GROUP <its-resource-group>
azd env set AZURE_SEARCH_SERVICE_NAME <your-search-service>        # optional: the knowledge base

# Staff sign-in (workforce tenant), from docs/identity-setup.md
azd env set ENTRA_TENANT_ID <tenant-id>
azd env set ENTRA_API_AUDIENCE api://<staff-api-app-id>
azd env set VITE_ENTRA_CLIENT_ID <staff-spa-client-id>
azd env set VITE_ENTRA_API_SCOPE api://<staff-api-app-id>/access_as_user

# Resident sign-in (External ID), optional, from scripts/setup_entra_external.sh
azd env set ENTRA_EXTERNAL_TENANT_ID <external-tenant-id>
azd env set ENTRA_EXTERNAL_TENANT_SUBDOMAIN <subdomain>
azd env set ENTRA_EXTERNAL_API_AUDIENCE api://<resident-api-app-id>
azd env set VITE_EXTERNAL_CLIENT_ID <resident-spa-client-id>
azd env set VITE_EXTERNAL_API_SCOPE api://<resident-api-app-id>/access_as_user
```

**3. Check before you create anything (optional, free).** `azd provision --preview` shows what would be created or
changed, without doing it.

**4. Deploy.**

```bash
azd up
```

The first run takes about 15 minutes. The `preprovision` hook checks the settings from step 2 and generates the two
secrets once (`PII_MAPPING_ENCRYPTION_KEY`, `RATE_LIMIT_HMAC_SECRET`); Bicep stores them in Key Vault. **Keep a copy
of the PII key** (`azd env get-value PII_MAPPING_ENCRYPTION_KEY`): without it, stored personal details can't be
revealed. The API runs the database migrations when it starts; production never gets the demo accounts.

**5. One-time steps after the first deployment** (the `postdeploy` hook prints them with your names filled in):

1. Add the web app's URL as a redirect URI of the staff and resident SPA app registrations.
2. Load the synthetic facility data for the incident team's tools:
   `az containerapp exec -g <group> -n <api-app> --command "python -m backend.app.cli seed-world"`
3. Make yourself the first administrator:
   `az containerapp exec -g <group> -n <api-app> --command "python -m backend.app.cli grant-role --provider entra --external-id <your-object-id> --role admin"`
   (your object id: `az ad signed-in-user show --query id -o tsv`).

**Later changes:** `azd deploy` rebuilds and deploys the apps; `azd provision` applies infrastructure changes.

## Cost

Approximate monthly cost in East US for a quiet demo environment, **before model usage**. Prices change: check the
[Azure pricing calculator](https://azure.microsoft.com/pricing/calculator/).

| Resource | Size | Roughly per month |
|---|---|---|
| Container Apps | Worker always on (0.5 vCPU, 1 GiB); API and web scale to zero | $25–35 |
| PostgreSQL flexible server | Burstable B1ms, 32 GB | $15–20 |
| Service Bus | Standard tier (topics need it) | about $10 |
| Container Registry | Basic | about $5 |
| Log Analytics + Application Insights | First 5 GB a month free | $0–5 |
| Key Vault | A few secret reads | under $1 |
| **Total** | | **about $60–75** |

**Model usage** comes on top and depends on traffic: each request is one to three classifier calls, and an escalated
incident uses about 150k tokens (mostly the Incident Manager on gpt-5.2).

## Removing everything

```bash
azd down            # deletes the resource group and everything in it
```

Your existing AI resources are not touched. Key Vault keeps deleted vaults for 7 days with purge protection on, so
the vault name stays reserved for a week; use a new environment name to redeploy sooner.

## How the code uses this

| Setting (set by Bicep) | Code |
|---|---|
| `DATABASE_ENTRA_AUTH=true` | [`db/engine.py`](../backend/app/db/engine.py): each new connection gets an Entra token as its password |
| `SERVICE_BUS_NAMESPACE` | [`worker.py`](../backend/app/worker.py) and [`services/service_bus.py`](../backend/app/services/service_bus.py): the outbox is published to the topic and each consumer receives its subscription |
| `AZURE_CLIENT_ID` | `DefaultAzureCredential` signs in as the user-assigned identity |
| Key Vault references | `PII_MAPPING_ENCRYPTION_KEY`, `RATE_LIMIT_HMAC_SECRET` |
| `APP_ENV=production`, `AUTH_ENABLED=true` | Real sign-in only; no development identities, no demo accounts |
