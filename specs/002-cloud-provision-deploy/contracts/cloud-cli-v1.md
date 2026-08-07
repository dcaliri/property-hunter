# Cloud CLI Command Contract (v1)

Interface contract for the operator-facing cloud scripts. Implementations MUST
honor these schemas so the behavior is testable and stable.

Contract file: `contracts/cloud-cli-v1.md` · Schema: `contracts/status-output-v1.schema.json`

## Conventions

- All scripts are POSIX `sh`/bash, run from the repository root.
- Exit code `0` = success; `1` = expected failure (invalid input, plan not
  clean, destroy refused); `2` = environment/tooling error (missing AWS CLI,
  OpenTofu, credentials).
- Every script supports `--help` and prints usage to stdout, exit 0.
- Output is human-readable by default. Commands marked *JSON-capable* accept
  `--json` and emit a single JSON document on stdout (see the schema).
- No script EVER requires a cloud-console action (spec FR-011).

## `bootstrap.sh` — one-time setup (run once per account)

Creates the two retained resources: the S3 state bucket and the EBS data
volume (bootstrap state, research §4).

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `--bucket <name>` | string | required | Globally unique S3 bucket name for state |
| `--volume-size <gb>` | integer | `20` | EBS gp3 size in GiB |
| `--az <zone>` | string | required | Availability zone (e.g. `us-east-1a`); must be used by every later `up` |
| `--state-dir <path>` | path | `infra/aws/bootstrap` | Where the bootstrap state lives |

- Idempotent: re-running with the same bucket/az yields the same resources.
- On success prints the bucket name and volume id (JSON-capable).

## `up.sh` — provision + deploy (US1)

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `--bucket <name>` | string | required | State bucket from bootstrap |
| `--ref <ref>` | string | `HEAD` | Git ref to deploy (rollback, research §9) |
| `--instance-type <type>` | string | `t4g.small` | EC2 instance type |
| `--auto-approve` | flag | off | Skip the apply confirmation |
| `--json` | flag | off | JSON output |

Behavior, in order:
1. Guarded stale-lock check + `force-unlock` on the state backend.
2. `tofu plan`; abort (exit 1) if the plan would create/destroy the retained
   resources (safety gate).
3. `tofu apply` (prompt unless `--auto-approve`).
4. Wait for the instance to register with SSM; then `send-command` the remote
   deploy (install docker, mount volume, fetch secrets, `compose up`).
5. Wait for the dashboard health check; print the tunnel command
   (`dashboard.sh`) on success.

Output: instance id, ref deployed, dashboard access command (JSON-capable).

## `down.sh` — deprovision (US2)

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `--bucket <name>` | string | required | State bucket from bootstrap |
| `--yes` | flag | off | Confirm destroy without prompting |
| `--json` | flag | off | JSON output |

Behavior, in order:
1. SSM `send-command` a graceful stop of the app (docker compose down).
2. `tofu plan -destroy`; abort (exit 1) if it would destroy the retained
   resources.
3. `tofu destroy` (confirm unless `--yes`).
4. Print the retained resources (bucket + volume) and the fact that data is
   preserved.

## `status.sh` — inventory + cost (FR-008; JSON-capable)

No required arguments. Prints the environment inventory (instance state, ref,
volume, bucket) and the estimated monthly cost table from `research.md` §10,
plus whether the estimate is above the USD 5 budget. Exit 0. Emits
`status-output-v1` JSON with `--json`.

## `dashboard.sh` — open the dashboard (US1/FR-011)

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `--instance <id>` | string | auto | Target instance id (from state output) |
| `--local-port` | integer | `9000` | Local port for the SSM port-forward tunnel |

Opens an SSM Session Manager port-forward (`instance:9000` → `localhost:
<local-port>`), then prints `http://localhost:<local-port>` and blocks while the
tunnel is up.

## `secrets.sh` — manage SSM parameters

| Subcommand | Behavior |
|---|---|
| `push [--env-file .env]` | Reads a local `.env` (gitignored, never committed), writes each key to `/property-hunter/<KEY>` as `SecureString` |
| `pull [--env-file .env]` | Fetches `/property-hunter/*` and writes the local `.env` |
| `list` | Lists parameter names under `/property-hunter/*` (no values) |

`push`/`pull` refuse to write a file containing values if a `.env` already
exists without `--force`.
