# Cost envelope and teardown

The initial project has a **US$10 total** allowance for a short experiment, not permission for ongoing hosting. Azure budget alerts are notifications, **not a hard spending cap**. Neither a Terraform expiry tag nor a workflow timeout deletes resources by itself.

## Planning estimate, not a bill

Official East US retail rates checked on 2026-11-12 for standard Consumption active usage:

- vCPU: $0.000024 per vCPU-second.
- Memory: $0.000003 per GiB-second.
- Requests: $0.40 per million.

At 0.25 vCPU and 0.5 GiB, one replica active for an hour is approximately **$0.027 in compute**, before requests, state storage, network charges, taxes or other account-specific charges. Published monthly free grants might reduce it; this estimate does not assume those grants are unused or available.

The lab does not configure Dedicated profiles, private endpoints, planned maintenance, ACR, Log Analytics ingestion or a database. Those choices avoid extra service/meter categories. Microsoft documents management charges for certain environment features even when an app uses Consumption; verify the actual offer and plan before applying. A retail estimate is not an all-in quote.

Operational target: one observed run under an hour, with **$1 reserved as a provisional planning envelope**, then complete teardown. The $10 ceiling still applies. If eligibility, required purchase, unexpected meter or final plan changes the envelope, stop and resolve it before allocating. Do not upgrade to a recurring paid plan just to force the demo through.

## Before allocating

1. Verify the subscription/account and current credit/spend/offer; record a baseline with private identifiers excluded from public proof.
2. Confirm image is anonymously pullable, region has Consumption availability and the reviewed plan contains only the named lab resources.
3. Keep the operator session and bootstrap state available for cleanup. Do not start a run you cannot monitor through removal.
4. Confirm app root max replicas is one, resource requests are 0.25 CPU/0.5 GiB, and no Dedicated profile was added.

## Normal cleanup order

1. Save evidence from the app and platform logs.
2. Destroy **infra/app** with the same backend and variables. Confirm no managed app or environment remains in Terraform state **and** independently query the exact resource group. Terraform output alone is not deletion proof.
3. With the owner/operator identity, destroy **infra/bootstrap** from its original local state. This removes state storage last, along with the disposable groups, deployment identity, federation and role assignments.
4. Query both exact resource groups with the intended subscription. Both should be absent. Check for resources that escaped into other groups only if deployment evidence points to them; never delete unrelated resources.
5. Record deletion times and later check Cost Management when usage has posted. “Resources deleted” and “final billed amount known” are separate facts.

## If the workflow stops or cleanup fails

The `always()` destroy step is a best-effort guard. Runner loss, cancellation, a hard job timeout or control-plane failure can prevent it. Do not assume a canceled workflow is cleaned up.

The first deployed run exposed two timing boundaries: public ingress was not yet ready after the deployment API completed, and Azure environment deletion exceeded the original ten minute cleanup allowance. The drill now waits up to 90 seconds for a stable healthy baseline before injection. Cleanup has a 20 minute step allowance within a 45 minute overall workflow bound. The destroy command receives an interrupt after 18 minutes, followed by a forced stop after another 30 seconds if necessary, leaving time for Terraform to release its state lock before the step deadline. These are finite waiting limits, not proof that Azure has finished deletion or a guarantee that cleanup or lock release will complete before timeout.

Use the authenticated operator context to inspect the same subscription and exact project-tagged groups. Reinitialize the app backend from the original bootstrap outputs, inspect state, then rerun app destroy. If state recovery is impossible, inspect and explicitly remove only the two known disposable lab groups through Azure's normal deletion path; never use a broad subscription sweep. Preserve proof and report what remains.

Do not remove the state store before app cleanup is reconciled. Do not leave a failed experiment running overnight. After Azure deletion is verified, remove or disable the GitHub environment variables used for the now-deleted identity.

## Sources

- [Azure Container Apps billing](https://learn.microsoft.com/en-us/azure/container-apps/billing)
- [Azure retail prices API](https://prices.azure.com/api/retail/prices)
- [Azure budgets and alerts](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets)
