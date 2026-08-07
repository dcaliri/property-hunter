# Data Model: Cloud Provision & Deploy

Phase 1 output of `/speckit.plan`. This feature adds **no new application
database tables** — the app's existing SQLite schema (listings, runs,
observations, predictions, detections, notifications, model_versions, pages,
baselines, zones, price_history) is unchanged. Instead, it introduces an
**infrastructure data model** describing the deployable environment and how the
app's data maps onto it.

## Entities

### Environment (ephemeral, destroyed weekly)

A running instance of the solution. Created and removed as a whole by the
`up.sh` / `down.sh` scripts.

| Attribute | Type | Notes |
|---|---|---|
| id | string | EC2 instance id, created each cycle |
| compute_type | string | `t4g.small` (free-trial) or `t4g.micro` (fallback) |
| availability_zone | string | Fixed to the retained volume's AZ (EBS is AZ-bound) |
| public_access | string | `ssm-tunnel` — no public IP (default) |
| state | string | `provisioned` → `deployed` → `deprovisioned` |
| app_ref | string | git ref deployed (rollback key, research §9) |
| data_volume_id | string | Reference to the retained PersistentVolume |
| state_bucket | string | Reference to the retained StateBucket |

### PersistentVolume (retained across `down`)

The only storage that survives deprovision; holds the app's SQLite database.

| Attribute | Type | Notes |
|---|---|---|
| id | string | EBS volume id, created once in the bootstrap state |
| volume_type | string | `gp3` (SQLite WAL-safe local block device) |
| size_gb | integer | 10–20 GiB ($0.08/GiB-mo) |
| availability_zone | string | Pinned; instance must launch in this AZ |
| mount_path | string | `/opt/property-hunter/data` on the instance |
| lifecycle | string | `retained` — never destroyed; detached/re-attached per cycle |
| tags | map | `Name=property-hunter-data` (used by the data-source lookup) |

### StateBucket (retained across `down`)

Terraform/OpenTofu state for the main environment stack.

| Attribute | Type | Notes |
|---|---|---|
| id | string | S3 bucket name, created once in the bootstrap state |
| versioning | bool | true (botched `apply` reversible) |
| encryption | string | server-side (SSE-S3 or KMS) |
| locking | string | S3-native `use_lockfile` (no DynamoDB) |

### Secret (app configuration)

Env-style values the app reads at runtime; stored outside the repository.

| Attribute | Type | Notes |
|---|---|---|
| name | string | `/property-hunter/<KEY>` (mirrors app env var name) |
| type | string | `SecureString` (KMS-encrypted, `aws/ssm` key) |
| source | string | Operator's local `.env` (never committed) |
| consumers | string[] | On-instance deploy script writes them to the app `.env` |

### Deployment

The running application version inside an Environment.

| Attribute | Type | Notes |
|---|---|---|
| ref | string | git tag/branch/sha pinned at deploy |
| services | string[] | `scheduler` (daily) + `ui` (dashboard, port 9000) |
| image | string | Built from the repo (same `Dockerfile`) on the instance |
| data_mount | string | PersistentVolume → container `/app/data` |

## Relationships

```
StateBucket ──holds──> Terraform state for Environment stack
PersistentVolume <──attaches── Environment (data_volume_id)
Environment ──runs──> Deployment (scheduler + ui containers)
Secret <──fetched-by── Deployment (writes app .env)
Deployment ──mounts── PersistentVolume (SQLite at /app/data)
```

- An Environment references exactly one PersistentVolume and one StateBucket;
  both are external to the Environment's lifecycle.
- Creating/destroying an Environment **never** creates/destroys the
  PersistentVolume or StateBucket.
- The app database lives on the PersistentVolume, so all existing tables'
  provenance and history survive `down`/`up` cycles unchanged (constitution III).

## Data flow across a weekly cycle

1. `up.sh` → main-stack `apply` → instance boots → user-data mounts the
   PersistentVolume, fetches Secrets, writes `.env`, starts Deployment.
2. Scheduled runs write observations/detections to SQLite on the volume.
3. `down.sh` → graceful stop → main-stack `destroy` → instance and attachment
   removed; PersistentVolume detaches and stays (data intact, storage still
   billed).
4. Next `up.sh` → new instance → same PersistentVolume reattached → prior data
   present.
