// The three apps (spec 9): the API, the incident worker and the web app, on one Container Apps
// environment. All run as the same user-assigned managed identity: images from the registry,
// secrets from Key Vault, PostgreSQL, Service Bus and the AI services, all with Entra ID.
// azd deploys new images into these apps (matched by the azd-service-name tag).
param name string
param location string
param tags object
param identityId string
param identityClientId string
param identityName string
param logsWorkspaceName string
param registryServer string
param appInsightsConnectionString string
param piiKeyUri string
param hmacSecretUri string
param postgresHost string
param databaseName string
param serviceBusHost string
param serviceBusTopic string
param aiServicesEndpoint string
param foundryEndpoint string
param searchEndpoint string
param incidentManagerModel string
param incidentSpecialistModel string
param classifierModel string
param entra object

resource logs 'Microsoft.OperationalInsights/workspaces@2026-03-01' existing = {
  name: logsWorkspaceName
}

resource environment 'Microsoft.App/managedEnvironments@2026-07-01' = {
  name: 'cae-${name}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

var apiName = 'ca-api-${name}'
var webName = 'ca-web-${name}'
var apiHost = '${apiName}.${environment.properties.defaultDomain}'
var webOrigin = 'https://${webName}.${environment.properties.defaultDomain}'
// azd replaces this with the built image on `azd deploy`.
var placeholderImage = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

var secrets = [
  { name: 'pii-mapping-encryption-key', keyVaultUrl: piiKeyUri, identity: identityId }
  { name: 'rate-limit-hmac-secret', keyVaultUrl: hmacSecretUri, identity: identityId }
]

// Settings shared by the API and the worker (backend/app/core/config.py).
var backendEnv = [
  { name: 'APP_ENV', value: 'production' }
  { name: 'AZURE_CLIENT_ID', value: identityClientId } // DefaultAzureCredential picks this identity
  { name: 'DATABASE_URL', value: 'postgresql+psycopg://${identityName}@${postgresHost}:5432/${databaseName}?sslmode=require' }
  { name: 'DATABASE_ENTRA_AUTH', value: 'true' }
  { name: 'SERVICE_BUS_NAMESPACE', value: serviceBusHost }
  { name: 'SERVICE_BUS_TOPIC', value: serviceBusTopic }
  { name: 'FOUNDRY_ENDPOINT', value: foundryEndpoint }
  { name: 'FOUNDRY_MODEL', value: classifierModel }
  { name: 'INCIDENT_MANAGER_MODEL', value: incidentManagerModel }
  { name: 'INCIDENT_SPECIALIST_MODEL', value: incidentSpecialistModel }
  { name: 'AZURE_LANGUAGE_ENDPOINT', value: aiServicesEndpoint }
  { name: 'CONTENT_SAFETY_ENDPOINT', value: aiServicesEndpoint }
  { name: 'VISION_ENDPOINT', value: aiServicesEndpoint }
  { name: 'SPEECH_ENDPOINT', value: aiServicesEndpoint }
  { name: 'DOCUMENT_INTELLIGENCE_ENDPOINT', value: aiServicesEndpoint }
  { name: 'TRANSLATOR_ENDPOINT', value: aiServicesEndpoint }
  { name: 'AZURE_SEARCH_ENDPOINT', value: searchEndpoint }
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
  { name: 'APPLICATIONINSIGHTS_ENTRA_AUTH', value: 'true' }
  { name: 'PII_MAPPING_ENCRYPTION_KEY', secretRef: 'pii-mapping-encryption-key' }
  { name: 'RATE_LIMIT_HMAC_SECRET', secretRef: 'rate-limit-hmac-secret' }
  // Real sign-in only; demo accounts are never created in production (migration 0004).
  { name: 'AUTH_ENABLED', value: 'true' }
  { name: 'SEED_DEMO_ACCOUNTS', value: 'false' }
  { name: 'ENTRA_TENANT_ID', value: entra.workforceTenantId }
  { name: 'ENTRA_API_AUDIENCE', value: entra.workforceApiAudience }
  { name: 'ENTRA_EXTERNAL_TENANT_ID', value: entra.externalTenantId }
  { name: 'ENTRA_EXTERNAL_TENANT_SUBDOMAIN', value: entra.externalTenantSubdomain }
  { name: 'ENTRA_EXTERNAL_API_AUDIENCE', value: entra.externalApiAudience }
  { name: 'LOG_FORMAT', value: 'json' }
]

var registries = [{ server: registryServer, identity: identityId }]
var identity = {
  type: 'UserAssigned'
  userAssignedIdentities: { '${identityId}': {} }
}

resource api 'Microsoft.App/containerApps@2026-07-01' = {
  name: apiName
  location: location
  tags: union(tags, { 'azd-service-name': 'api' })
  identity: identity
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      registries: registries
      secrets: secrets
    }
    template: {
      containers: [
        {
          name: 'api'
          image: placeholderImage
          // Migrations run before the API starts; an advisory lock in migrations/env.py makes
          // concurrent replicas wait for each other instead of racing.
          command: ['/bin/sh', '-c', 'alembic upgrade head && exec uvicorn backend.app.main:create_app --factory --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips "*"']
          env: concat(backendEnv, [
            { name: 'ALLOWED_HOSTS', value: '["${apiHost}"]' }
            { name: 'FRONTEND_ORIGINS', value: '["${webOrigin}"]' }
          ])
          resources: { cpu: json('0.5'), memory: '1Gi' }
          probes: [
            { type: 'Liveness', httpGet: { path: '/health/live', port: 8000 }, periodSeconds: 30 }
            { type: 'Startup', httpGet: { path: '/health/live', port: 8000 }, periodSeconds: 10, failureThreshold: 30 }
          ]
        }
      ]
      scale: { minReplicas: 0, maxReplicas: 3 }
    }
  }
}

resource worker 'Microsoft.App/containerApps@2026-07-01' = {
  name: 'ca-worker-${name}'
  location: location
  tags: union(tags, { 'azd-service-name': 'worker' })
  identity: identity
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: registries
      secrets: secrets
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: placeholderImage
          command: ['python', '-m', 'backend.app.worker']
          env: backendEnv
          resources: { cpu: json('0.5'), memory: '1Gi' }
        }
      ]
      // One worker publishes the outbox and receives the subscriptions; it can't scale to zero.
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}

resource web 'Microsoft.App/containerApps@2026-07-01' = {
  name: webName
  location: location
  tags: union(tags, { 'azd-service-name': 'web' })
  identity: identity
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        allowInsecure: false
      }
      registries: registries
    }
    template: {
      containers: [
        {
          name: 'web'
          image: placeholderImage
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
        }
      ]
      scale: { minReplicas: 0, maxReplicas: 2 }
    }
  }
}

output apiUrl string = 'https://${apiHost}'
output webUrl string = webOrigin
output apiName string = api.name
output workerName string = worker.name
output webName string = web.name
output environmentName string = environment.name
