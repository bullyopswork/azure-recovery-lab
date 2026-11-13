terraform {
  required_version = ">= 1.17.2, < 2.0.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "= 5.13.0"
    }
  }
}
provider "azurerm" {
  features {}
  resource_provider_registrations = "none"
  storage_use_azuread             = true
}
variable "suffix" {
  type        = string
  description = "Unique lowercase suffix for this one lab;8 to12 alphanumeric characters."
  validation {
    condition     = can(regex("^[a-z0-9]{8,12}$", var.suffix))
    error_message = "Use8 to12 lowercase letters/numbers."
  }
}
variable "location" {
  type    = string
  default = "eastus"
}
variable "github_repository" {
  type    = string
  default = "bullyopswork/azure-recovery-lab"
}
variable "github_oidc_subject" {
  type        = string
  description = "Exact GitHub OIDC sub claim for this repository's azure-demo environment, including immutable IDs when enabled."
  nullable    = false
  validation {
    condition     = replace(var.github_oidc_subject, "/@[0-9]+/", "") == "repo:${var.github_repository}:environment:azure-demo"
    error_message = "The exact subject must identify github_repository and the azure-demo environment; wildcards and other repositories/environments are not allowed."
  }
}
data "azurerm_client_config" "current" {}

resource "azurerm_resource_group" "control" {
  name     = "rg-recoverylab-control-${var.suffix}"
  location = var.location
  tags     = { project = "azure-recovery-lab", purpose = "temporary-demo" }
}
resource "azurerm_resource_group" "app" {
  name     = "rg-recoverylab-app-${var.suffix}"
  location = var.location
  tags     = { project = "azure-recovery-lab", purpose = "temporary-demo" }
}
resource "azurerm_storage_account" "state" {
  name                            = "rlstate${var.suffix}"
  resource_group_name             = azurerm_resource_group.control.name
  location                        = var.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  shared_access_key_enabled       = false
  allow_nested_items_to_be_public = false
  tags                            = { project = "azure-recovery-lab" }
}
resource "azurerm_role_assignment" "bootstrap_state" {
  scope                = azurerm_storage_account.state.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}
resource "azurerm_storage_container" "state" {
  name                  = "tfstate"
  storage_account_id    = azurerm_storage_account.state.id
  container_access_type = "private"
  depends_on            = [azurerm_role_assignment.bootstrap_state]
}
resource "azurerm_user_assigned_identity" "deployment" {
  name                = "recoverylab-github"
  location            = var.location
  resource_group_name = azurerm_resource_group.control.name
}
resource "azurerm_federated_identity_credential" "github" {
  name                      = "github-azure-demo"
  user_assigned_identity_id = azurerm_user_assigned_identity.deployment.id
  audience                  = ["api://AzureADTokenExchange"]
  issuer                    = "https://token.actions.githubusercontent.com"
  subject                   = var.github_oidc_subject
}
resource "azurerm_role_assignment" "deployment" {
  scope                = azurerm_resource_group.app.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.deployment.principal_id
}
resource "azurerm_role_assignment" "deployment_state" {
  scope                = azurerm_storage_account.state.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.deployment.principal_id
}
output "app_resource_group" { value = azurerm_resource_group.app.name }
output "state_resource_group" { value = azurerm_resource_group.control.name }
output "state_storage_account" { value = azurerm_storage_account.state.name }
output "client_id" { value = azurerm_user_assigned_identity.deployment.client_id }
output "tenant_id" { value = data.azurerm_client_config.current.tenant_id }
output "subscription_id" { value = data.azurerm_client_config.current.subscription_id }
