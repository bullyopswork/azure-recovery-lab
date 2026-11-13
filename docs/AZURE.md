# Temporary Azure deployment

This runbook is for one disposable lab. It is not a deployment to an existing business resource group. Review [cost and cleanup](COST_AND_CLEANUP.md) first.

## Prerequisites

- An active Azure subscription, billing/credit eligibility checked in that account, and an owner/operator allowed to create resource groups and role assignments.
- Azure CLI with `az containerapp exec`, Terraform 1.17.2, Python and GitHub CLI.
- This repository in your own GitHub account. The example is `bullyopswork/azure-recovery-lab`; update the bootstrap repository variable and workflow owner guard for a fork.
- A public linux/amd64 GHCR image, pinned to the digest produced by the publish workflow. Anonymous access must pass `scripts/verify_public_image.py`.

No reusable Azure client secret or GitHub PAT is placed in the repository. The operator signs into Azure normally. The initial bootstrap uses that operator's permissions, not GitHub's deployment identity.

## Bootstrap once

1. Select the intended subscription with `az login` and `az account set`. Verify its account, tenant and offer. Do not silently use another default subscription.
2. Register `Microsoft.App`, `Microsoft.OperationalInsights`, `Microsoft.Storage` and `Microsoft.ManagedIdentity` if required for this subscription. Registration is an operator action; the restricted deployment identity cannot register providers.
3. Choose a fresh suffix of 8 to 12 lowercase alphanumeric characters. Inspect `infra/bootstrap/main.tf`. Check the repository's current OIDC configuration with `gh api repos/OWNER/REPO/actions/oidc/customization/sub`. When `use_immutable_subject` is true, use the returned `sub_claim_prefix` followed by `:environment:azure-demo`. Otherwise, verify the exact subject emitted by the runner; do not assume a legacy format for a customized configuration. Set `TF_VAR_github_oidc_subject` to that exact claim. Plan this root with `TF_VAR_suffix` and the intended `ARM_SUBSCRIPTION_ID`, then review the plan before apply.
4. Apply bootstrap. Keep its local Terraform state in protected local storage, out of Git. Role-assignment propagation can take time; diagnose an authorization error rather than broadening scopes.
5. Create the GitHub environment **azure-demo**, restrict it to main, and configure the following environment secrets and variables from bootstrap outputs. Store all three Azure identity IDs as secrets so GitHub masks them before step headers, login output and Terraform logs are displayed. Use canonical lowercase UUID values. Do not put these three values in ordinary variables or add a fallback to variables.

| GitHub environment input | Kind | Terraform output |
| --- | --- | --- |
| `AZURE_CLIENT_ID` | Secret | `client_id` |
| `AZURE_TENANT_ID` | Secret | `tenant_id` |
| `AZURE_SUBSCRIPTION_ID` | Secret | `subscription_id` |
| `APP_RESOURCE_GROUP` | Variable | `app_resource_group` |
| `STATE_RESOURCE_GROUP` | Variable | `state_resource_group` |
| `STATE_STORAGE_ACCOUNT` | Variable | `state_storage_account` |

These identifiers are not passwords. Secret storage is used for automatic log masking, not to create a reusable Azure credential. Missing or noncanonical identity inputs fail before Azure login. Do not restore IDs for an identity that has already been deleted just to run this check.

The OIDC trust uses the exact configured subject, issuer `https://token.actions.githubusercontent.com`, and audience `api://AzureADTokenExchange`. An immutable subject has the form `repo:OWNER@OWNER_ID/REPO@REPO_ID:environment:azure-demo`; a legacy subject omits the numeric IDs. Terraform rejects subjects for another repository or environment. Contributor is scoped only to the lab app resource group; blob access is scoped only to its state storage account. There is no subscription wide Contributor grant.

The first live login exposed this distinction: the repository emitted an immutable subject while the original bootstrap trusted the legacy form. Azure refused that login before app deployment. Match the actual claim rather than disabling immutable subjects or broadening permissions.

The bootstrap state must remain available for final teardown. It is intentionally separate from the app's private remote state so that state storage can be removed last.

## Publish and verify the image

Run **Publish portfolio container** on main. Read the immutable `ghcr.io/...@sha256:...` reference from the successful run summary. A new GHCR package can default to private; check the exact package visibility before changing it to public. Do not pass registry credentials to the app as a workaround for a private portfolio image.

```sh
python3 scripts/verify_public_image.py ghcr.io/OWNER/NAME@sha256:DIGEST
```

Replace the placeholders with the real lowercase name and 64-character digest. The verifier makes an anonymous token request, verifies actual manifest and configuration bytes against their digests, checks the configuration's linux/amd64 architecture, and does not print/store the transient pull token.

## Run one cloud experiment

Manually dispatch **Temporary Azure recovery demo** with that image and CLI faults enabled. The workflow initializes Entra-authenticated blob state, reviews the Terraform plan in its log, applies only the app/environment, runs one bounded experiment, then attempts `terraform destroy` in an `always()` step.

Each workflow execution writes raw evidence into a fresh runner temporary directory. Before upload, `scripts/ci_privacy.py` creates a separate JSON copy with the configured Azure identity IDs redacted, plus a manifest binding the workflow run, attempt, source commit and raw/published file hashes. An experiment that did not run is labeled `NOT_RUN`. Only that new sanitized directory is eligible for upload. Historical checked in results and reviewed receipts are not included. GitHub log masking does not modify artifact files; the artifact sanitizer is a separate boundary. Preserve raw copies privately when needed for later review and make any sanitization explicit.

The experiment checks the project tag, resource-group prefix, exact live ingress, one replica, selected revision/container, configured liveness probe and opt-in fault flag. It uses the constant remote command `python -m recovery_lab.fault`; it never calls `az containerapp restart` or changes the revision. It requires a matching boot-bound injection acknowledgement, an HTTP failure, and healthy HTTP from a different boot UUID.

The measurement starts before CLI exec, so it includes connection setup. It is **not** pure platform restart time. Platform logs are captured separately. A new boot proves replacement, not why the platform replaced it; only a run-window review of system events can verify the liveness cause.

## Finish

Download the run's evidence before retention expires. Review platform events and the saved cleanup result. Use the owner context to confirm the app resource group has no app/environment left, then destroy the bootstrap root to remove the two disposable groups, identity/role assignments and state storage. See the cleanup document for failure cases.

No Azure success claim belongs in the README until a real run, cause review and teardown have been verified.
