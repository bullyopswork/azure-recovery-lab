terraform {
  required_version = ">= 1.17.2, < 2.0.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "= 5.13.0"
    }
  }
  backend "azurerm" {
    use_azuread_auth = true
  }
}

provider "azurerm" {
  features {}
  resource_provider_registrations = "none"
  storage_use_azuread             = true
}

variable "resource_group_name" {
  type        = string
  description = "Dedicated app resource group created by this lab's bootstrap."
  validation {
    condition     = can(regex("^rg-recoverylab-app-[a-z0-9]{8,12}$", var.resource_group_name))
    error_message = "Use the dedicated rg-recoverylab-app-<8 to12 character suffix> group."
  }
}
variable "container_image" {
  type        = string
  description = "Publicly pullable OCI image, pinned to a SHA256 digest."
  validation {
    condition     = can(regex("^ghcr.io/[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9._/-]*@sha256:[a-f0-9]{64}$", var.container_image))
    error_message = "Supply an immutable ghcr.io image@sha256:digest, not a mutable tag."
  }
}
variable "enable_faults" {
  type        = bool
  default     = false
  description = "Opt-in CLI-only unhealthy fault for a temporary controlled experiment."
}
variable "expires_at" {
  type        = string
  description = "UTC experiment expiry tag. This label does NOT automatically delete resources."
}

data "azurerm_resource_group" "lab" {
  name = var.resource_group_name
}

resource "azurerm_container_app_environment" "lab" {
  name                = "recoverylab-env"
  resource_group_name = data.azurerm_resource_group.lab.name
  location            = data.azurerm_resource_group.lab.location
  # Stream-only logs avoid a separate ingestion workspace for this short demo.
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
  tags = { project = "azure-recovery-lab", expires_at = var.expires_at }
}

resource "azurerm_container_app" "lab" {
  name                         = "recoverylab-app"
  container_app_environment_id = azurerm_container_app_environment.lab.id
  resource_group_name          = data.azurerm_resource_group.lab.name
  revision_mode                = "Single"
  max_inactive_revisions       = 1
  workload_profile_name        = "Consumption"
  tags                         = { project = "azure-recovery-lab", expires_at = var.expires_at }
  ingress {
    external_enabled           = true
    allow_insecure_connections = false
    target_port                = 8080
    transport                  = "http"
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
  template {
    min_replicas                     = 1
    max_replicas                     = 1
    termination_grace_period_seconds = 10
    container {
      name   = "web"
      image  = var.container_image
      cpu    = 0.25
      memory = "0.5Gi"
      env {
        name  = "LAB_ENVIRONMENT"
        value = "azure"
      }
      env {
        name  = "LAB_STATE_DIR"
        value = "/tmp/recovery-lab"
      }
      env {
        name  = "LAB_ENABLE_FAULTS"
        value = var.enable_faults ? "1" : "0"
      }
      startup_probe {
        transport               = "HTTP"
        port                    = 8080
        path                    = "/health/live"
        interval_seconds        = 2
        failure_count_threshold = 30
        timeout                 = 1
      }
      liveness_probe {
        transport               = "HTTP"
        port                    = 8080
        path                    = "/health/live"
        initial_delay           = 5
        interval_seconds        = 3
        failure_count_threshold = 3
        timeout                 = 1
      }
      readiness_probe {
        transport               = "HTTP"
        port                    = 8080
        path                    = "/health/ready"
        interval_seconds        = 2
        failure_count_threshold = 1
        success_count_threshold = 1
        timeout                 = 1
      }
    }
  }
}

output "url" {
  value = "https://${azurerm_container_app.lab.latest_revision_fqdn}"
}
output "app_name" {
  value = azurerm_container_app.lab.name
}
