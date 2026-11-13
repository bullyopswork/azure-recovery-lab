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

## Current Azure attempts

The live deployment and GitHub OIDC exchange have succeeded. Recovery is still unverified. The first deployed run stopped at its initial HTTP health check before ingress was ready. A later run stopped in its first Azure CLI app inspection. The original helper discarded CLI stderr, so that second failure's underlying cause is unknown. Neither failed test injected a fault.

The inspection helper now reports fixed, allowlisted diagnostic labels rather than arbitrary stderr, which can contain private values. It retries recognized temporary read failures at most three times, with a 15 second command timeout and short backoff. Authentication, authorization and unknown failures stop immediately. The helper accepts only app inspection and replica listing; fault injection is never retried through it. A successful read and all configuration and healthy baseline checks are still required before injection.

## Demo outline

1. Show repository status, actual CI result and architecture.
2. Show current healthy dashboard and boot identity.
3. Trigger the bounded experiment from the CLI, not a hidden/public control.
4. Show failure, new identity and run-bound evidence.
5. For Azure, show platform-cause evidence and independently verified teardown.

Do not label a local recording as Azure. Do not expose passwords, tokens, subscription identifiers or a personal mailbox in a public demonstration.
