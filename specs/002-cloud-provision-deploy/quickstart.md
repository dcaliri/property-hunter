# Quickstart: Cloud Provision & Deploy

Validation guide for the cloud lifecycle feature. Implementation details live
in `tasks.md` and the code; this file proves the feature works end-to-end.
Command contracts: `contracts/cloud-cli-v1.md`; output schema:
`contracts/status-output-v1.schema.json`.

## Prerequisites

- An AWS account with billing and IAM permissions (bootstrap/`apply`/`destroy`).
- AWS CLI v2 + Session Manager plugin, configured with credentials.
- OpenTofu (or Terraform — commands identical) ≥ 1.7, plus `docker`/`git`.
- The one-time bootstrap resources (state bucket + EBS volume) exist.
- Budget: the cost model in `research.md` §10 (< USD 5/mo target).

## Local setup

```bash
# One-time per account (resources that `down` never removes):
./scripts/cloud/bootstrap.sh --bucket <unique-name> --az us-east-1a --volume-size 20

# Push secrets from your local .env (never committed) to SSM:
./scripts/cloud/secrets.sh push

# Static validation of the IaC (no AWS resources created):
./scripts/cloud/check.sh          # tofu validate + fmt -check + sh -n on scripts
```

## Validation scenarios

### Scenario A — Provision + deploy from a clean state (US1)

```bash
./scripts/cloud/up.sh --bucket <unique-name> --auto-approve
./scripts/cloud/dashboard.sh      # opens the SSM tunnel, prints http://localhost:9000
```

Expected: a new instance is created and the app deploys (scheduler + ui
containers); the dashboard loads at `http://localhost:9000` and shows run
history; `up.sh` printed the instance id and ref. Provision+deploy completes in
under 15 minutes (SC-001). A scheduled run fires at the next configured time
and its results appear in the dashboard.

### Scenario B — Deprovision everything (US2)

```bash
./scripts/cloud/down.sh --bucket <unique-name> --yes
./scripts/cloud/status.sh --json
```

Expected: `down.sh` stops the app and destroys all environment resources in
under 10 minutes (SC-002); `status.sh` shows `environment: null` with only the
retained bucket + volume present; a cloud-console resource list shows no project
compute/network resources left (SC-003/SC-006 — no console steps were needed).

### Scenario C — Data survives a full cycle (US3, SC-005)

```bash
./scripts/cloud/up.sh --bucket <unique-name> --auto-approve
./scripts/cloud/dashboard.sh      # verify prior listings/runs are still there
```

Expected: after Scenario B's deprovision, a fresh `up.sh` reattaches the same
EBS volume and the dashboard shows the previously collected listings and run
history — 100% of prior data present (SC-005).

### Scenario D — Cost visibility (FR-008, SC-004)

```bash
./scripts/cloud/status.sh --json
```

Expected: the JSON `cost` object reports an estimated monthly cost at or below
the USD 5 budget (`over_budget: false`) during the free-trial window, with the
assumption list explaining the estimate.

### Scenario E — Rollback (FR-010)

```bash
git tag v1.0-collect-only            # an earlier known-good ref
./scripts/cloud/up.sh --bucket <unique-name> --ref v1.0-collect-only --auto-approve
```

Expected: the environment runs the pinned ref's application version; the
dashboard still shows all retained data. A mid-cycle rollback uses
`./scripts/cloud/deploy.sh --ref <ref>` (SSM `send-command`), without
re-provisioning.

### Scenario F — Idempotence & failure recovery (FR-005)

```bash
./scripts/cloud/up.sh --bucket <unique-name> --auto-approve   # run again while up
./scripts/cloud/down.sh --bucket <unique-name> --yes          # with no instance
```

Expected: re-running `up.sh` with the environment already up is a no-op plan
(exit 0, no new resources); `down.sh` with nothing provisioned exits 0 cleanly.
Simulate a mid-`apply` failure by killing `up.sh`; the next `up.sh` recovers
without orphaned or duplicated resources (guarded stale-lock check).

## Not in scope for v1 validation

- Multiple regions/AZs (single pinned AZ by design).
- Public-IP dashboard access (SSM tunnel only; no EIP — see `research.md` §8).
- AWS Secrets Manager / rotation (SSM Parameter Store is used — `research.md` §6).
- Cost alerts/budgets automation (estimated cost only via `status.sh`).
