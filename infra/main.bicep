// Infrastructure as Code for the serverless URL shortener.
// Creates: Storage account (function state + links table), Log Analytics,
// Application Insights, a Consumption (serverless) plan and the Function App.
//
// Deploy:
//   az group create -n rg-url-shortener -l uksouth
//   az deployment group create -g rg-url-shortener -f infra/main.bicep

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Short prefix used in resource names (lowercase letters/digits).')
@maxLength(10)
param prefix string = 'urlshort'

var suffix = uniqueString(resourceGroup().id)
var storageName = toLower('${prefix}${take(suffix, 8)}')
var functionAppName = '${prefix}-func-${suffix}'

resource storage 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
  }
}

resource tableService 'Microsoft.Storage/storageAccounts/tableServices@2023-01-01' = {
  parent: storage
  name: 'default'
}

resource linksTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-01-01' = {
  parent: tableService
  name: 'links'
}

resource logs 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: '${prefix}-logs-${suffix}'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${prefix}-ai-${suffix}'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
  }
}

resource plan 'Microsoft.Web/serverfarms@2023-01-01' = {
  name: '${prefix}-plan-${suffix}'
  location: location
  kind: 'linux'
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  properties: { reserved: true }
}

var storageConnection = 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'

resource functionApp 'Microsoft.Web/sites@2023-01-01' = {
  name: functionAppName
  location: location
  kind: 'functionapp,linux'
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      appSettings: [
        { name: 'AzureWebJobsStorage', value: storageConnection }
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
        { name: 'LINKS_STORAGE_CONNECTION', value: storageConnection }
        { name: 'LINKS_TABLE_NAME', value: linksTable.name }
      ]
    }
  }
}

output functionAppName string = functionApp.name
output apiBaseUrl string = 'https://${functionApp.properties.defaultHostName}/api'
