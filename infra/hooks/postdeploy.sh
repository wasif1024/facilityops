#!/bin/sh
# azd postdeploy hook: where the apps are, and the one-time steps after the first deployment.
set -e
value() { azd env get-value "$1" 2>/dev/null || true; }
group=$(value AZURE_RESOURCE_GROUP); api=$(value API_APP_NAME)

cat <<MSG

FacilityOps AI is deployed.
  Web app: $(value WEB_URL)
  API:     $(value API_BASE_URL)

One-time steps (docs/deployment.md, step 5):
  1. Add $(value WEB_URL) as a redirect URI of the staff and resident SPA app registrations.
  2. Load the synthetic facility data for the incident team's tools:
       az containerapp exec -g $group -n $api --command "python -m backend.app.cli seed-world"
  3. Make yourself the first administrator (your Entra object id: az ad signed-in-user show --query id -o tsv):
       az containerapp exec -g $group -n $api --command "python -m backend.app.cli grant-role --provider entra --external-id <object-id> --role admin"
MSG
