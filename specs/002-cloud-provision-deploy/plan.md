# Implementation Plan: Cloud Provision & Deploy

**Branch**: `002-cloud-provision-deploy` | **Date**: 2026-08-07 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-cloud-provision-deploy/spec.md`

## Summary

Give the operator a cheap, CLI-only cloud lifecycle for Property Hunter on AWS:
one command to provision and deploy a running environment (daily scheduler +
dashboard + persistent SQLite), one command to deprovision it, and retained
state (EBS volume + S3 state bucket) so weekly cycles lose no data. Research
decided: a single small ARM EC2 instance (free trial `t4g.small`, fallback
`t4g.micro`) running the existing Docker image via docker compose; a retained
`gp3` EBS volume referenced only through a data source in a bootstrap state; an
S3-backed OpenTofu (Terraform-compatible) main state; secrets in free SSM
Parameter Store; deploy via cloud-init user-data + SSM `send-command` with no
SSH keys; dashboard accessed through SSM Session Manager port forwarding (no
public IPv4 charge). See [research.md](research.md).

## Technical Context

**Language/Version**: Shell (POSIX bash) for the CLI scripts; HCL for
OpenTofu/Terraform (≥ 1.7). Application unchanged (Python 3.12).

**Primary Dependencies**: OpenTofu (or Terraform) with the AWS provider; AWS
CLI v2 + Session Manager plugin; Docker + compose plugin on the instance
(installed via cloud-init); existing app deps (httpx, sklearn, APScheduler,
pydantic, dotenv).

**Storage**: EBS `gp3` volume (10–20 GiB, retained across `down`) for the
SQLite database; versioned S3 bucket for Terraform state; SSM Parameter Store
for secrets. No new app tables (see [data-model.md](data-model.md)).

**Testing**: pytest (existing suite + new IaC/script validation tests); static
`tofu validate` / `tofu fmt -check` / shell syntax checks; the full provision/
destroy cycle is validated manually via [quickstart.md](quickstart.md) against a
real AWS account.

**Target Platform**: AWS EC2 (ARM/Graviton Linux), Docker container.

**Project Type**: CLI + infrastructure-as-code (dev-ops feature over the
existing pipeline app).

**Performance Goals**: Provision+deploy from scratch < 15 min; deprovision
< 10 min (spec SC-001/SC-002). No latency SLA for the dashboard (personal use).

**Constraints**: < USD 5/mo (free-trial window, part-time cycles); CLI-only, no
cloud-console steps; no SSH keys / no port 22; no Elastic IP; retained data
across cycles; idempotent provision/deprovision; secrets never committed.

**Scale/Scope**: single operator, one region, one pinned AZ, ~10 resources.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Assessment |
|---|---|
| I. Production-Ready by Default | PASS — deployed env exposes health (dashboard health check, `status.sh`) and structured logs; deploy is declarative and reproducible. |
| II. Legal & Ethical Scraping | PASS — cloud deploy runs the same polite collection config (delay, user-agent, bounded pages); no scraping behavior changes. |
| III. Data Integrity & Provenance | PASS — SQLite (provenance, price history) lives on a retained EBS volume; survives every cycle; `down` never touches it. |
| IV. Test-First Quality | PASS — static IaC validation (`tofu validate`/`fmt`), shell syntax checks, and documented manual lifecycle scenarios (quickstart A–F). Live `apply`/`destroy` cannot run in CI (needs AWS), so it is validated manually per quickstart. |
| V. Modular Pipeline, Explicit Contracts | PASS — pipeline unchanged; the cloud layer has explicit CLI/JSON contracts (`contracts/`). |
| Security & Privacy | PASS — secrets in SSM Parameter Store (SecureString), never in the repo; no SSH keys; dashboard only reachable via authenticated SSM tunnel. |

No gate violations; the Complexity Tracking table is therefore left empty.

## Project Structure

### Documentation (this feature)

```text
specs/002-cloud-provision-deploy/
├── plan.md               # This file (/speckit.plan command output)
├── research.md           # Phase 0 output (decisions + 2026 pricing)
├── spec.md               # /speckit.specify output
├── data-model.md         # Phase 1 output (infrastructure entities)
├── quickstart.md         # Phase 1 output (validation scenarios A–F)
├── contracts/
│   ├── cloud-cli-v1.md             # CLI command schemas
│   └── status-output-v1.schema.json
└── checklists/
    └── requirements.md   # specification quality checklist
```

### Source Code (repository root)

```text
infra/
└── aws/
    ├── bootstrap/          # one-time, never destroyed
    │   ├── main.tf         # S3 state bucket + EBS data volume (tagged)
    │   └── outputs.tf      # bucket name, volume id, az
    ├── main.tf             # ephemeral stack: VPC, subnet, IGW, SG, IAM, instance
    ├── data.tf             # data-source lookup of the retained volume + state
    ├── outputs.tf          # instance id, ref, tunnel command
    ├── variables.tf        # bucket, az, instance_type, app_ref, region
    └── backend.tf          # S3 backend (versioned, use_lockfile)

scripts/
└── cloud/
    ├── bootstrap.sh        # one-time bucket + volume
    ├── up.sh               # provision + deploy (US1)
    ├── down.sh             # deprovision (US2)
    ├── status.sh           # inventory + estimated cost (FR-008)
    ├── dashboard.sh        # SSM port-forward tunnel to the dashboard
    ├── secrets.sh          # push/pull/list SSM params
    ├── deploy.sh           # mid-cycle redeploy / rollback at a git ref
    ├── check.sh            # static validation (tofu validate/fmt, sh -n)
    └── remote/             # scripts shipped to the instance (user-data + SSM)
        └── remote-deploy.sh  # docker install, volume mount, .env, compose up

docker-compose.cloud.yml   # cloud override: scheduler + ui services (port 9000)
.env.cloud.example         # documented cloud env keys (no secrets)
```

**Structure Decision**: single repository with a dedicated `infra/` directory
for the IaC and a `scripts/cloud/` directory for the operator CLI. The app code
is untouched — the feature is additive (infra + scripts + one compose override).

## Complexity Tracking

> No constitution violations — table intentionally left empty.
