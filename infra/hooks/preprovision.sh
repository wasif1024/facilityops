#!/bin/sh
# azd preprovision hook: make sure the required settings exist, and generate the app secrets once.
# Secrets live only in the azd environment (.azure/, gitignored) and in Key Vault.
set -e

value() { azd env get-value "$1" 2>/dev/null || true; }

missing=""
for name in AZURE_AI_SERVICES_NAME AZURE_AI_RESOURCE_GROUP ENTRA_TENANT_ID ENTRA_API_AUDIENCE VITE_ENTRA_CLIENT_ID VITE_ENTRA_API_SCOPE; do
  [ -n "$(value "$name")" ] || missing="$missing $name"
done
if [ -n "$missing" ]; then
  echo "Missing settings:$missing"
  echo "Set each with: azd env set <NAME> <value>   (see docs/deployment.md, step 2)"
  exit 1
fi

if [ -z "$(value PII_MAPPING_ENCRYPTION_KEY)" ]; then
  # A Fernet key: 32 random bytes, URL-safe base64.
  azd env set PII_MAPPING_ENCRYPTION_KEY "$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')" >/dev/null
  echo "Generated PII_MAPPING_ENCRYPTION_KEY (stored in Key Vault on provision). Keep a backup: without it, revealed details can't be decrypted."
fi
if [ -z "$(value RATE_LIMIT_HMAC_SECRET)" ]; then
  azd env set RATE_LIMIT_HMAC_SECRET "$(python3 -c 'import secrets; print(secrets.token_hex(32))')" >/dev/null
  echo "Generated RATE_LIMIT_HMAC_SECRET."
fi
