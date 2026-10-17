// PostgreSQL with Microsoft Entra authentication only: no password exists. The apps' managed
// identity is the Entra administrator (it runs the migrations); see docs/deployment.md.
param name string
param location string
param tags object
param identityName string
param identityPrincipalId string
param databaseName string = 'facilityops'

resource server 'Microsoft.DBforPostgreSQL/flexibleServers@2025-08-01' = {
  name: 'psql-${name}'
  location: location
  tags: tags
  sku: { name: 'Standard_B1ms', tier: 'Burstable' }
  properties: {
    version: '17'
    storage: { storageSizeGB: 32, autoGrow: 'Enabled' }
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
    network: { publicNetworkAccess: 'Enabled' }
    authConfig: {
      activeDirectoryAuth: 'Enabled'
      passwordAuth: 'Disabled'
      tenantId: subscription().tenantId
    }
  }
}

// Reachable only from Azure services (Container Apps); no client IPs.
resource azureServices 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2025-08-01' = {
  parent: server
  name: 'AllowAzureServices'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

resource admin 'Microsoft.DBforPostgreSQL/flexibleServers/administrators@2025-08-01' = {
  parent: server
  name: identityPrincipalId
  properties: {
    principalType: 'ServicePrincipal'
    principalName: identityName
    tenantId: subscription().tenantId
  }
  dependsOn: [azureServices]
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2025-08-01' = {
  parent: server
  name: databaseName
  properties: { charset: 'UTF8', collation: 'en_US.utf8' }
  dependsOn: [admin]
}

output host string = server.properties.fullyQualifiedDomainName
output databaseName string = database.name
