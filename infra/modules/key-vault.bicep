// Application secrets (spec 8): the PII mapping encryption key and the rate-limit HMAC secret.
// RBAC mode; only the apps' managed identity can read secrets (Key Vault Secrets User).
param name string
param location string
param tags object
param identityPrincipalId string
@secure()
param piiMappingEncryptionKey string
@secure()
param rateLimitHmacSecret string

resource vault 'Microsoft.KeyVault/vaults@2026-05-15' = {
  name: 'kv-${take(replace(name, '-', ''), 21)}'
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    enablePurgeProtection: true
    publicNetworkAccess: 'Enabled'
  }
}

resource piiKey 'Microsoft.KeyVault/vaults/secrets@2026-05-15' = {
  parent: vault
  name: 'pii-mapping-encryption-key'
  properties: { value: piiMappingEncryptionKey }
}

resource hmacSecret 'Microsoft.KeyVault/vaults/secrets@2026-05-15' = {
  parent: vault
  name: 'rate-limit-hmac-secret'
  properties: { value: rateLimitHmacSecret }
}

resource secretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, identityPrincipalId, 'KeyVaultSecretsUser')
  scope: vault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output piiKeyUri string = piiKey.properties.secretUri
output hmacSecretUri string = hmacSecret.properties.secretUri
output name string = vault.name
