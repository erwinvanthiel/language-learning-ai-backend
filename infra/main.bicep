targetScope = 'resourceGroup'

@description('Azure region for web, storage, search, and messaging resources.')
param location string = resourceGroup().location
@description('Azure region for the Azure OpenAI account.')
param openAiLocation string = 'swedencentral'
param apiName string = 'language-learning-ai-api-evth'
param apiDevName string = 'language-learning-ai-api-dev-evth'
param translateName string = 'language-learning-ai-translate-evth'
param translateDockerImage string = 'libretranslate/libretranslate:latest'
param staticWebName string = 'language-learning-ai-web-evth'
param remindersName string = 'language-learning-ai-reminders-evth'
param remindersDevName string = 'language-learning-ai-reminders-dev-evth'
param appPlanName string = 'language-learning-ai-plan'
param functionPlanName string = 'ASP-languagelearningairg-0471'
param storageName string = 'languageaistorageevth'
param openAiName string = 'language-learning-ai-openai-evth'
param searchName string = 'language-learning-ai-search-evth'
param serviceBusName string = 'language-learning-ai-sb-evth'
param appPlanSku string = 'B1'
param searchSku string = 'basic'

resource appPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: appPlanName
  location: location
  kind: 'linux'
  properties: { reserved: true }
  sku: {
    name: appPlanSku
    tier: appPlanSku == 'B1' ? 'Basic' : 'PremiumV3'
  }
}

resource api 'Microsoft.Web/sites@2023-12-01' = [for name in [apiName, apiDevName]: {
  name: name
  location: location
  kind: 'app,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: appPlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      alwaysOn: appPlanSku != 'F1'
      minTlsVersion: '1.2'
    }
  }
}]

resource translate 'Microsoft.Web/sites@2023-12-01' = {
  name: translateName
  location: location
  kind: 'app,linux,container'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: appPlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'DOCKER|${translateDockerImage}'
      minTlsVersion: '1.2'
    }
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}

resource tableService 'Microsoft.Storage/storageAccounts/tableServices@2023-05-01' = {
  name: 'default'
  parent: storage
}

resource usersTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  name: 'Users'
  parent: tableService
}

resource messagesTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  name: 'Messages'
  parent: tableService
}

resource serviceBus 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: serviceBusName
  location: location
  sku: { name: 'Standard' }
  properties: { publicNetworkAccess: 'Enabled' }
}

resource remindersQueue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  name: 'reminders'
  parent: serviceBus
  properties: {
    maxDeliveryCount: 10
    lockDuration: 'PT1M'
  }
}

resource openAi 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: openAiName
  location: openAiLocation
  kind: 'OpenAI'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: openAiName
    publicNetworkAccess: 'Enabled'
  }
}

resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: searchName
  location: location
  sku: { name: searchSku }
  identity: { type: 'SystemAssigned' }
  properties: {
    partitionCount: 1
    replicaCount: 1
    publicNetworkAccess: 'enabled'
  }
}

resource staticWeb 'Microsoft.Web/staticSites@2022-09-01' = {
  name: staticWebName
  location: location
  sku: { name: 'Free' }
  properties: { stagingEnvironmentPolicy: 'Enabled' }
}

resource functionPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: functionPlanName
  location: location
  kind: 'functionapp'
  sku: { name: 'FC1' }
  properties: { reserved: true }
}

resource reminderApp 'Microsoft.Web/sites@2023-12-01' = [for name in [remindersName, remindersDevName]: {
  name: name
  location: location
  kind: 'functionapp,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: functionPlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'Python|3.12'
      appSettings: [
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
        { name: 'SERVICE_BUS_NAMESPACE', value: serviceBus.name }
        { name: 'SERVICE_BUS_QUEUE', value: remindersQueue.name }
      ]
    }
  }
}]

output apiPrincipalId string = api[0].identity.principalId
output apiDevPrincipalId string = api[1].identity.principalId
output reminderPrincipalId string = reminderApp[0].identity.principalId
output storageEndpoint string = storage.properties.primaryEndpoints.table
output openAiEndpoint string = 'https://${openAi.properties.customSubDomainName}.openai.azure.com/'
