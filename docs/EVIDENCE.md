# Evidence, not just a green screenshot

## Local proof

`evidence/local-recovery.json` names a unique run and its event file. Each trial must have healthy HTTP before injection, HTTP 503 from the targeted old boot, a supervisor `liveness_probe` restart request, and healthy HTTP from a different boot. Cleanup must pass. The event count and run identity are checked.

The current three trials took 1.151, 1.142 and 1.153 seconds at a 100 ms sampling interval. This is a finite process-supervision experiment on one developer machine, using the standard-library WSGI server. It does not validate Gunicorn or Azure. Linux CI separately builds and smoke-tests the actual production image.

## Cloud proof

The cloud script writes `RUNNING` before doing the experiment and atomically changes it to `FAIL` or `PASS_RECOVERY_CAUSE_REQUIRES_REVIEW`. It cannot silently reuse a previous run's PASS. A successful measurement requires:

1. The exact tagged app, ingress, image/revision and one-replica configuration.
2. Three consecutive healthy HTTP samples from the same boot A, after a bounded startup wait. Startup failures remain separate baseline evidence and cannot count toward recovery.
3. CLI fault acknowledgement explicitly naming boot A.
4. An observed non-200/unreachable interval.
5. Healthy HTTP from boot B, with A != B and unchanged revision/image.

There is no manual restart call in this path. Still, replacement alone does not prove the cause. Review captured system events in the **same run window**, match the revision/replica, and identify the liveness-failure/restart sequence before marking `platform_liveness_cause_verified` true in a separate reviewed receipt. Preserve the raw machine result rather than editing a failed result into success.

If logs do not establish cause, say so. Report `azure_recovery_observed` separately from cause verification. Include replica changes, HTTP sampling interval, source commit, image digest, timings, exact experiment definition and cleanup receipt. Recovery-from-exec-start is not comparable to the local injection-to-ready timing without accounting for setup and polling differences.

## Accepted Azure result, November 13, 2026

[Workflow 43466690841](https://github.com/bullyopswork/azure-recovery-lab/actions/runs/43466690841), exact harness source `e5c8d757c46db64541d97268055185de6b1f6ebd`, passed every step. Raw run `56ee628dcc774783b2c03df6a5261d56` records **43.158 seconds** from monotonic exec launch to completion of the first healthy response from the new boot. The response sample timestamps mark request start, so subtracting them does not reproduce that completed observation metric. CLI setup, network, ingress and polling are included. This is one controlled trial, not pure platform restart latency or an SLA.

The healthy baseline boot was `18adbf37-fab5-4a09-86df-f2eb1f2b6823`; the CLI acknowledgement names that exact boot. A post acknowledgement HTTP 503 was recorded, followed by healthy boot `405a0982-6ba3-4cff-b85c-3b27e97a5068`. Revision, replica and image stayed the same. The platform restarted the container within the existing replica. A separate HTTP observer corroborated the same two boot IDs and measured 38.771 seconds between its first failed request and first healthy request from the new boot; that is a different metric.

The matching platform events show liveness HTTP 503 at 16:55:55 and 16:55:58 UTC, an explicit will be restarted event, `ProbeFailure` termination at 16:55:58, and the container starting at 16:56:09. The later startup probe error is after restart and is not its trigger. App level traffic/update messages at 16:56:01 explicitly state that no revision restart or provisioning was needed; those events are retained, not omitted.

The [raw result](../evidence/azure-recovery.json) is unchanged, including its deliberately false pre-review cause flag and `PASS_RECOVERY_CAUSE_REQUIRES_REVIEW` label. The [separate reviewed receipt](../evidence/azure-recovery-review.json) records cause verification and final teardown. Published system events retain selected event fields and unchanged messages; the complete original artifact is retained by the linked workflow. SHA256 provenance is in the reviewed receipt.

Automatic app/environment cleanup passed after about 25 minutes. Independent checks then confirmed exact environment absence, an empty app group, empty remote managed state and an available backend lease. Only then was the reviewed nine delete bootstrap plan applied. Both groups, identity, state storage, federation and scoped roles were independently verified absent. Final billing remains unverified until Azure posts usable usage data.

Earlier attempts stopped before injection because of OIDC trust mismatch, ingress startup timing, an undiagnosed CLI inspection error, and a hard deployment timeout with a stale state lock. Their failed evidence is preserved and their outcomes remain distinct in [verification.json](../evidence/verification.json).

## Public log privacy and future artifact provenance

Seven historical workflow log archives contained Azure subscription and tenant identifiers. No authentication credential was identified in the repository/history/Actions review. Those log archives were removed after their private backups were verified. Workflow results, downloadable artifacts, the accepted raw result and the published cause evidence remain intact. Commit authorship was not rewritten.

Future cloud runs require the three Azure identity IDs as GitHub environment secrets for automatic log masking. Their artifact files are sanitized separately, with raw and published hashes and explicit workflow/source identity in `workflow-evidence.json`. Fresh output directories prevent skipped or failed executions from uploading historical portfolio successes. A skipped experiment has `NOT_RUN` status, not a copied PASS. The accepted historical cloud trial remains bound to its original harness source; this privacy change is not another cloud recovery test.

## Demo outline

1. Show repository status, actual CI result and architecture.
2. Show current healthy dashboard and boot identity.
3. Trigger the bounded experiment from the CLI, not a hidden/public control.
4. Show failure, new identity and run-bound evidence.
5. For Azure, show platform-cause evidence and independently verified teardown.

Do not label a local recording as Azure. Do not expose passwords, tokens, subscription identifiers or a personal mailbox in a public demonstration.
