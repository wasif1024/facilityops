// Least-privilege access for the apps' identity to the EXISTING AI resources (deployed in their
// own resource group; this template never creates or changes them).
//   AI Services: Cognitive Services User (Language, Content Safety, Vision, Speech, Document
//   Intelligence, Translator) and Foundry User (models through the Foundry project).
//   AI Search: Search Index Data Reader (the Foundry IQ knowledge base, read-only).
param aiServicesName string
param searchServiceName string = ''
param identityPrincipalId string

resource aiServices 'Microsoft.CognitiveServices/accounts@2026-07-01' existing = {
  name: aiServicesName
}

resource search 'Microsoft.Search/searchServices@2025-05-01' existing = if (!empty(searchServiceName)) {
  name: searchServiceName
}

var aiRoles = {
  CognitiveServicesUser: 'a97b65f3-24c7-4388-baec-2e87135dc908'
  FoundryUser: '53ca6127-db72-4b80-b1b0-d745d6d5456d'
}

resource aiAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for role in items(aiRoles): {
  name: guid(aiServices.id, identityPrincipalId, role.key)
  scope: aiServices
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role.value)
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
  }
}]

resource searchReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(searchServiceName)) {
  name: guid(search.id, identityPrincipalId, 'SearchIndexDataReader')
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '1407120a-92aa-4202-b7e9-c0e197c71c8f')
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output aiServicesEndpoint string = 'https://${aiServicesName}.cognitiveservices.azure.com/'
output searchEndpoint string = empty(searchServiceName) ? '' : 'https://${searchServiceName}.search.windows.net'
