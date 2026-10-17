// Events between the API and the incident worker (spec 6.2): one topic, one subscription per
// consumer, filtered by event type. Entra only (local SAS auth off). Mirrors the delivery rules
// of the local outbox: 3 deliveries, then the dead-letter queue, which hands the incident to a person.
@minLength(3)
param name string
param location string
param tags object
param identityPrincipalId string
param topicName string = 'facilityops-events'

// Which event types each consumer receives; keep in step with services/incident_worker.py and
// services/incident_actions.py (and the fake in tests/integration/test_service_bus.py).
var consumers = {
  'incident-team': ['intake.escalated', 'incident.plan_decided']
  'action-executor': ['incident.plan_decided']
}

resource namespace 'Microsoft.ServiceBus/namespaces@2026-01-01' = {
  name: 'sb-${name}'
  location: location
  tags: tags
  sku: { name: 'Standard', tier: 'Standard' }
  properties: {
    disableLocalAuth: true
    minimumTlsVersion: '1.2'
  }
}

resource topic 'Microsoft.ServiceBus/namespaces/topics@2026-01-01' = {
  parent: namespace
  name: topicName
  properties: {
    requiresDuplicateDetection: true
    duplicateDetectionHistoryTimeWindow: 'PT1H'
    defaultMessageTimeToLive: 'P7D'
  }
}

resource subscriptions 'Microsoft.ServiceBus/namespaces/topics/subscriptions@2026-01-01' = [for consumer in items(consumers): {
  parent: topic
  name: consumer.key
  properties: {
    maxDeliveryCount: 3
    // The incident team takes minutes; the worker also renews the lock while it works.
    lockDuration: 'PT5M'
    deadLetteringOnMessageExpiration: true
  }
}]

// Replace each subscription's catch-all rule with a filter on the event type (the message subject).
resource filters 'Microsoft.ServiceBus/namespaces/topics/subscriptions/rules@2026-01-01' = [for (consumer, i) in items(consumers): {
  parent: subscriptions[i]
  name: '$Default'
  properties: {
    filterType: 'SqlFilter'
    sqlFilter: {
      sqlExpression: 'sys.Label IN (${join(map(consumer.value, t => '\'${t}\''), ', ')})'
    }
  }
}]

resource sender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(namespace.id, identityPrincipalId, 'ServiceBusDataSender')
  scope: namespace
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39')
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource receiver 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(namespace.id, identityPrincipalId, 'ServiceBusDataReceiver')
  scope: namespace
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0')
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output namespaceHost string = '${namespace.name}.servicebus.windows.net'
output topicName string = topic.name
