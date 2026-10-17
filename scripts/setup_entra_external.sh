#!/usr/bin/env bash
# Configure an Entra External ID tenant for FacilityOps AI residents.
#
# Prerequisites (see docs/identity-setup.md):
#   1. An external tenant, created in the Entra admin center.
#   2. Signed in to it:  az login --tenant <external-tenant-id> --allow-no-subscriptions
#
# Usage:
#   scripts/setup_entra_external.sh --subdomain <name> [--redirect-uri <url> ...]
#
#   --subdomain     the part before .onmicrosoft.com / .ciamlogin.com, e.g. facilityopsresidents
#   --redirect-uri  where the web app runs; default http://localhost:5173 (repeatable)
#
# Creates, or reuses when they already exist:
#   - "FacilityOps API (residents)": the API app; exposes the access_as_user scope, v2 tokens
#   - "FacilityOps web (residents)": the single-page app; redirect URIs, admin consent
#   - "FacilityOps residents sign-up": an email + password sign-up and sign-in user flow
# Then prints the backend and frontend settings to paste.

set -euo pipefail

GRAPH="https://graph.microsoft.com/v1.0"
GRAPH_APP_ID="00000003-0000-0000-c000-000000000000"
OPENID_SCOPE_ID="37f7f235-527c-4136-accd-4a02d197296e"
OFFLINE_ACCESS_SCOPE_ID="7427e0e9-2fba-42fe-b0c0-848c9e6a8182"
API_NAME="FacilityOps API (residents)"
SPA_NAME="FacilityOps web (residents)"
FLOW_NAME="FacilityOps residents sign-up"

SUBDOMAIN=""
REDIRECT_URIS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --subdomain) SUBDOMAIN="$2"; shift 2 ;;
    --redirect-uri) REDIRECT_URIS+=("$2"); shift 2 ;;
    -h | --help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done
[ -n "$SUBDOMAIN" ] || { echo "error: --subdomain is required" >&2; exit 1; }
[ ${#REDIRECT_URIS[@]} -gt 0 ] || REDIRECT_URIS=("http://localhost:5173")

info() { echo "==> $*"; }
graph() { az rest --only-show-errors --method "$1" --url "$GRAPH$2" ${3:+--headers Content-Type=application/json --body "$3"}; }
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1], {}, {'d': d}))" "$1"; }

TENANT_ID="$(az account show --query tenantId -o tsv)"
# Domain names are case-insensitive (FacilityOps.onmicrosoft.com == facilityops.onmicrosoft.com).
SUBDOMAIN="$(printf '%s' "$SUBDOMAIN" | tr '[:upper:]' '[:lower:]')"
DOMAINS="$(graph GET "/domains" | json "','.join(x['id'].lower() for x in d['value'])")"
case ",$DOMAINS," in
  *",$SUBDOMAIN.onmicrosoft.com,"*) ;;
  *) echo "error: signed-in tenant $TENANT_ID has no domain $SUBDOMAIN.onmicrosoft.com (found: $DOMAINS)." >&2
     echo "       Sign in to the external tenant: az login --tenant <id> --allow-no-subscriptions" >&2
     exit 1 ;;
esac
info "external tenant $TENANT_ID ($SUBDOMAIN.ciamlogin.com)"

app_id_by_name() {
  graph GET "/applications?\$filter=displayName%20eq%20'$(python3 -c 'import sys,urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$1")'" |
    json "d['value'][0]['appId'] if d['value'] else ''"
}

ensure_service_principal() {
  local app_id="$1" existing
  existing="$(graph GET "/servicePrincipals?\$filter=appId%20eq%20'$app_id'" | json "d['value'][0]['id'] if d['value'] else ''")"
  if [ -z "$existing" ]; then
    existing="$(graph POST "/servicePrincipals" "{\"appId\":\"$app_id\"}" | json "d['id']")"
  fi
  echo "$existing"
}

# 1. The API. v2 access tokens are required: the backend validates the v2 issuer.
API_APP_ID="$(app_id_by_name "$API_NAME")"
if [ -z "$API_APP_ID" ]; then
  info "creating $API_NAME"
  SCOPE_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
  API_APP_ID="$(graph POST "/applications" "$(cat <<JSON
{
  "displayName": "$API_NAME",
  "signInAudience": "AzureADMyOrg",
  "api": {
    "requestedAccessTokenVersion": 2,
    "oauth2PermissionScopes": [{
      "id": "$SCOPE_ID",
      "value": "access_as_user",
      "type": "User",
      "isEnabled": true,
      "adminConsentDisplayName": "Use FacilityOps as the signed-in resident",
      "adminConsentDescription": "Lets the FacilityOps web app call the API on behalf of the signed-in resident.",
      "userConsentDisplayName": "Use FacilityOps as you",
      "userConsentDescription": "Lets the FacilityOps web app report and track maintenance requests for you."
    }]
  }
}
JSON
)" | json "d['appId']")"
  API_OBJECT_ID="$(graph GET "/applications(appId='$API_APP_ID')" | json "d['id']")"
  graph PATCH "/applications/$API_OBJECT_ID" "{\"identifierUris\":[\"api://$API_APP_ID\"]}" >/dev/null
else
  info "reusing $API_NAME ($API_APP_ID)"
fi
SCOPE_ID="$(graph GET "/applications(appId='$API_APP_ID')" |
  json "[s['id'] for s in d['api']['oauth2PermissionScopes'] if s['value']=='access_as_user'][0]")"
API_SP_ID="$(ensure_service_principal "$API_APP_ID")"

# 2. The single-page app.
REDIRECTS_JSON="$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "${REDIRECT_URIS[@]}")"
SPA_APP_ID="$(app_id_by_name "$SPA_NAME")"
SPA_BODY="$(cat <<JSON
{
  "displayName": "$SPA_NAME",
  "signInAudience": "AzureADMyOrg",
  "spa": {"redirectUris": $REDIRECTS_JSON},
  "requiredResourceAccess": [
    {"resourceAppId": "$API_APP_ID", "resourceAccess": [{"id": "$SCOPE_ID", "type": "Scope"}]},
    {"resourceAppId": "$GRAPH_APP_ID", "resourceAccess": [
      {"id": "$OPENID_SCOPE_ID", "type": "Scope"},
      {"id": "$OFFLINE_ACCESS_SCOPE_ID", "type": "Scope"}
    ]}
  ]
}
JSON
)"
if [ -z "$SPA_APP_ID" ]; then
  info "creating $SPA_NAME"
  SPA_APP_ID="$(graph POST "/applications" "$SPA_BODY" | json "d['appId']")"
else
  info "reusing $SPA_NAME ($SPA_APP_ID); updating redirect URIs"
  SPA_OBJECT_ID="$(graph GET "/applications(appId='$SPA_APP_ID')" | json "d['id']")"
  graph PATCH "/applications/$SPA_OBJECT_ID" "$SPA_BODY" >/dev/null
fi
SPA_SP_ID="$(ensure_service_principal "$SPA_APP_ID")"
GRAPH_SP_ID="$(ensure_service_principal "$GRAPH_APP_ID")"

# 3. Admin consent, so residents aren't asked to approve permissions on first sign-in.
grant() {
  local resource="$1" scope="$2" existing
  existing="$(graph GET "/oauth2PermissionGrants?\$filter=clientId%20eq%20'$SPA_SP_ID'%20and%20resourceId%20eq%20'$resource'" |
    json "d['value'][0]['id'] if d['value'] else ''")"
  if [ -z "$existing" ]; then
    graph POST "/oauth2PermissionGrants" \
      "{\"clientId\":\"$SPA_SP_ID\",\"consentType\":\"AllPrincipals\",\"resourceId\":\"$resource\",\"scope\":\"$scope\"}" >/dev/null
  fi
}
info "granting admin consent"
grant "$API_SP_ID" "access_as_user"
grant "$GRAPH_SP_ID" "openid offline_access"

# 4. The sign-up and sign-in user flow: email + password, email verified with a code.
FLOW_ID="$(graph GET "/identity/authenticationEventsFlows" |
  json "next((f['id'] for f in d['value'] if f.get('displayName')=='$FLOW_NAME'), '')")"
if [ -z "$FLOW_ID" ]; then
  info "creating user flow $FLOW_NAME"
  graph POST "/identity/authenticationEventsFlows" "$(cat <<JSON
{
  "@odata.type": "#microsoft.graph.externalUsersSelfServiceSignUpEventsFlow",
  "displayName": "$FLOW_NAME",
  "conditions": {"applications": {"includeApplications": [{"appId": "$SPA_APP_ID"}]}},
  "onAuthenticationMethodLoadStart": {
    "@odata.type": "#microsoft.graph.onAuthenticationMethodLoadStartExternalUsersSelfServiceSignUp",
    "identityProviders": [{"id": "EmailPassword-OAUTH"}]
  },
  "onInteractiveAuthFlowStart": {
    "@odata.type": "#microsoft.graph.onInteractiveAuthFlowStartExternalUsersSelfServiceSignUp",
    "isSignUpAllowed": true
  },
  "onAttributeCollection": {
    "@odata.type": "#microsoft.graph.onAttributeCollectionExternalUsersSelfServiceSignUp",
    "attributes": [
      {"id": "email", "displayName": "Email Address", "userFlowAttributeType": "builtIn", "dataType": "string"},
      {"id": "displayName", "displayName": "Display Name", "userFlowAttributeType": "builtIn", "dataType": "string"}
    ],
    "attributeCollectionPage": {"views": [{"inputs": [
      {"attribute": "email", "label": "Email address", "inputType": "text", "hidden": true,
       "editable": false, "writeToDirectory": true, "required": true},
      {"attribute": "displayName", "label": "Your name", "inputType": "text", "hidden": false,
       "editable": true, "writeToDirectory": true, "required": true}
    ]}]}
  }
}
JSON
)" >/dev/null
else
  info "reusing user flow $FLOW_NAME (make sure $SPA_NAME is added to it)"
fi

cat <<EOF

Done. Paste these settings:

# backend .env
AUTH_ENABLED=true
ENTRA_EXTERNAL_TENANT_ID=$TENANT_ID
ENTRA_EXTERNAL_TENANT_SUBDOMAIN=$SUBDOMAIN
ENTRA_EXTERNAL_API_AUDIENCE=$API_APP_ID

# frontend/.env.local
VITE_AUTH_MODE=entra
VITE_EXTERNAL_CLIENT_ID=$SPA_APP_ID
VITE_EXTERNAL_TENANT_SUBDOMAIN=$SUBDOMAIN
VITE_EXTERNAL_TENANT_ID=$TENANT_ID
VITE_EXTERNAL_API_SCOPE=api://$API_APP_ID/access_as_user
EOF
