# Research: Cloud Provision & Deploy (Phase 0)

Output of `/speckit.plan` Phase 0. Each section records a **Decision**, its
**Rationale**, and **Alternatives considered**. Prices verified 2026-08-07
(us-east-1) unless flagged; AWS pricing changes — re-verify before use.

## 1. Compute: a single small EC2 instance

**Decision**: One always-on EC2 instance (ARM/Graviton, `t4g.small` during the
free trial through 2026-12-31, else `t4g.micro`) running the existing Docker
image via docker compose. The daily scheduler needs the instance up 24/7 so the
cron fires; the dashboard is low traffic.

**Rationale**:
- t4g.micro ≈ $6.13/mo on-demand (2 vCPU / 1 GiB); t4g.small ≈ $12.26/mo but is
  **free under a 750 h/mo trial for existing customers until 2026-12-31**
  (verified on the AWS t4g page / re:Post).
- Fargate (smallest 0.25 vCPU / 0.5 GB) is ~$7–9/mo at steady state — 2–3× the
  EC2 cost — **and cannot attach an existing EBS volume** (restore-from-snapshot
  only; service volumes deleted on termination), which breaks the retained-data
  model (research §2).
- ARM/graviton is cheaper than x86 for this workload; the app is pure Python
  (no arch-specific binaries).
- Part-time usage (Mon–Thu, destroy Fri) makes on-demand cost ~$3.50/mo for
  t4g.micro, well within budget.

## 2. Persistent data: retained EBS gp3 volume

**Decision**: One `gp3` EBS volume (min 1 GiB, size for headroom ~10–20 GiB,
$0.08/GiB-mo ≈ $0.80–1.60/mo) holding the SQLite database. The volume is **never
destroyed**; it detaches on deprovision and reattaches on the next provision.

**Rationale**:
- The app's database is a **SQLite file in WAL mode** — the SQLite docs state WAL
  is not safe over a network filesystem. **EFS is therefore disqualified** (also
  ~4× the price of gp3). EBS behaves like a local block device; WAL works.
- EBS bills while detached (~$0.80–1.60/mo during `down`), which is acceptable
  and is the cheapest way to keep data across cycles.
- EBS volumes are AZ-bound: the instance must launch in the volume's
  availability zone every cycle (pin the subnet to one AZ).

## 3. IaC tool: OpenTofu (Terraform-compatible HCL)

**Decision**: **OpenTofu** (Linux Foundation, MPL-2.0, ~drop-in with Terraform)
with the standard AWS provider. Terraform works identically if preferred.

**Rationale**:
- Terraform/OpenTofu's `plan`/`apply`/`destroy` semantics map directly to the
  provision/deprovision requirement; both are auditable HCL.
- Terraform now ships under the HashiCorp **BSL 1.1** (IBM) — source-available,
  not OSI-open. OpenTofu is the fully-open, CNCF-hosted continuation with the
  same state format and extra features (native state encryption).
- CloudFormation: AWS-native and rollback-capable but verbose YAML without
  loops and 500-resource/stack caps — overkill for a ~10-resource personal
  project. CDK adds an app-synth layer and slower deploys.

## 4. Retaining the volume across `terraform destroy`

**Decision**: Split into **two states**.
1. **Bootstrap state** (created once, never destroyed): the S3 state bucket +
   the EBS volume, tagged `Name=property-hunter-data`.
2. **Main state** (provisioned/destroyed weekly): VPC, subnet, IGW, security
   group, IAM role, instance, and an `aws_volume_attachment` whose `volume_id`
   comes from a **data source** lookup, never a managed `aws_ebs_volume`.

**Rationale** (verified against provider behavior):
- Destroying `aws_volume_attachment` only **detaches**; the volume is deleted
   only if the `aws_ebs_volume` *resource* is destroyed. Referencing it only via
   a data source means `destroy` can never touch it.
- `lifecycle.prevent_destroy` alone **hard-errors `terraform destroy`** (forces
   `state rm`/`import` gymnastics each cycle) — rejected.
- `aws_volume_attachment.skip_destroy` skips the *detach* but does not protect
   the volume resource — rejected.

## 5. State storage: versioned S3 bucket

**Decision**: S3 backend, bucket versioning enabled + server-side encryption,
`use_lockfile = true`. Cost ≈ $0.01/mo; effectively free.

**Rationale**:
- Removes "state lives on my laptop" risk (lost laptop → orphaned resources).
- S3-native locking (`use_lockfile`) is GA — **no DynamoDB table needed**.
- A crashed `up` can leave a stale `.tflock`; the `up.sh` script runs a guarded
  `force-unlock` first (cheap insurance). A clean `down` releases the lock
  normally.
- The bucket and the EBS volume are the **only two things `down` never removes**.

## 6. Secrets: SSM Parameter Store (Standard tier)

**Decision**: Store the app's ~10 env-style secrets as `SecureString` params
under the path `/property-hunter/*` (free, KMS-encrypted with the default
`aws/ssm` key). The on-instance deploy script runs
`aws ssm get-parameters-by-path --path /property-hunter --with-decryption`
and writes the app's `.env`. App code is unchanged.

**Rationale**: Parameter Store Standard is free (up to 10k params, 4 KB each)
and correct for static env-style values. **Secrets Manager costs $0.40/secret/
month** (~$4/mo for 10) and buys rotation/cross-account features we don't need.

## 7. Deploy without SSH keys

**Decision**: Two mechanisms, **no SSH and no keypair resource**:
- **cloud-init user-data** on the fresh instance (each `up` creates a new
  instance): installs Docker + compose plugin, pulls the image, fetches `.env`
  from SSM, mounts the EBS volume, and runs `docker compose up -d`.
- **SSM `send-command`** for mid-week redeploys/rollback and debugging
  (`aws ssm start-session`), via an IAM instance role with
  `AmazonSSMManagedInstanceCore`. No inbound ports.

**Rationale**: user-data runs on every fresh boot (matches the weekly recreate
cycle); SSM gives an authenticated re-run path without opening port 22, storing
keys, or adding inbound rules. SSM is free.

## 8. Dashboard access & the public-IPv4 charge

**Decision**: **No public IP by default.** The dashboard is reached through
**SSM Session Manager port forwarding** (`start-session` + the
`AWS-StartPortForwardingSession` document maps instance port 9000 →
`localhost:9000`). `dashboard.sh` opens the tunnel and prints the URL.

**Rationale**:
- Public IPv4 (including auto-assigned) now bills ~$0.005/h ≈ **$3.65/mo**.
  Auto-assigned IPs also *change each cycle* (URL bookmarks break). An Elastic
  IP left idle while `down` bills ~$3.65/mo — rejected.
- SSM port-forwarding is free, needs no security-group exposure, and yields a
  stable `localhost` URL. Trade-off: a tunnel command is required to view the
  dashboard (one extra script).

## 9. Rollback

**Decision**: Deployments are **pinned to a git ref** (default: current HEAD).
`up.sh --ref <ref>` provisions and deploys that exact revision; a mid-cycle
rollback uses `deploy.sh --ref <ref>` via SSM `send-command` (checkout + rebuild
+ `compose up`). Re-provisioning at an old ref also works because infra is
reproducible from HCL and the data volume persists.

## 10. Cost model

| Item | $/mo (24/7) | $/mo (Mon–Thu) |
|---|---|---|
| Compute `t4g.small` (free trial to 2026-12-31) | 0 | 0 |
| Compute `t4g.micro` on-demand (post-trial) | ~6.13 | ~3.50 |
| EBS gp3 20 GiB (always billed) | ~1.60 | ~1.60 |
| Public IPv4 (avoided via SSM tunnel) | 0 | 0 |
| S3 state + SSM params | ~0 | ~0 |
| **Total with free trial** | **~1.60** | **~1.60** |
| **Total post-trial (t4g.micro)** | **~7.73** | **~5.10** |

- SC-004 (< USD 5/mo) is met during the free trial and comfortably met with
  part-time cycles; post-trial 24/7 running is ~$7.7/mo (still cheap, flagged in
  Assumptions).
- Network: first 100 GB/mo egress is always free; this app uses negligible data.
- Not fully verified: exact current OpenTofu/Terraform patch versions, exact
  t3/t4g micro 2026 hourly rates, and whether the post-2025-07 credit free tier
  covers public IPv4 (we avoid it anyway).

## Open items resolved

All `[NEEDS CLARIFICATION]` candidates from the spec had reasonable defaults and
are now concrete: EC2 vs Fargate → EC2; retained data → EBS volume in a
bootstrap state; IaC tool → OpenTofu (Terraform-compatible); secrets → SSM
Parameter Store; deploy → cloud-init + SSM, no SSH; dashboard → SSM tunnel, no
public IP.
