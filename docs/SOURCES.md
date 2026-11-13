# Sources and versions

Implementation references checked against vendor documentation on 2026-11-12:

- [Container Apps health probes](https://learn.microsoft.com/en-us/azure/container-apps/health-probes): startup, liveness and readiness configuration.
- [Container Apps billing](https://learn.microsoft.com/en-us/azure/container-apps/billing): Consumption meters, free grants and management-feature caveats.
- [Azure GitHub OIDC](https://learn.microsoft.com/en-us/azure/developer/github/connect-from-azure-openid-connect): federation issuer, subject/audience and short-lived identity exchange.
- [AzureRM 5.13.0 provider documentation](https://github.com/hashicorp/terraform-provider-azurerm/tree/v5.13.0/website/docs): container app/environment, managed identity and federation schema.
- [Azure CLI containerapp commands](https://learn.microsoft.com/en-us/cli/azure/containerapp): interactive exec, replica inspection and system-log streaming.
- [GitHub container registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry): image publication and package visibility.

Terraform 1.17.2 and AzureRM 5.13.0 are pinned. Provider lockfiles are committed. Workflow actions are pinned to exact release commits with version comments. Python production dependencies are pinned with hashes in `requirements.txt`. The base Python image tag is currently mutable; the built GHCR artifact is selected by its immutable digest for Azure.

References describe the intended behavior. They are not evidence of a deployment having occurred.
