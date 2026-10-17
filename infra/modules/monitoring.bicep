// Logs and traces: Log Analytics for Container Apps logs, Application Insights for OpenTelemetry.
param name string
param location string
param tags object
param identityPrincipalId string

resource logs 'Microsoft.OperationalInsights/workspaces@2026-03-01' = {
  name: 'log-${name}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${name}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
    // Sign-in with Entra only: no instrumentation key ingestion.
    DisableLocalAuth: true
  }
}

// Entra-authenticated ingestion: the apps' identity may publish telemetry, nothing else.
resource publisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appInsights.id, identityPrincipalId, 'MonitoringMetricsPublisher')
  scope: appInsights
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '3913510d-42f4-4e42-8a64-420c390055eb')
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output logsWorkspaceName string = logs.name
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output appInsightsId string = appInsights.id
