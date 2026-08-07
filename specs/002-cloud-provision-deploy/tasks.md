---

description: "Task list for the cloud provision & deploy feature implementation"
---

# Tasks: Cloud Provision & Deploy

**Input**: Design documents from `/specs/002-cloud-provision-deploy/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Static IaC validation (OpenTofu validate/fmt, shell syntax) is included per the plan's Constitution Check; the live provision/destroy lifecycle is validated manually via quickstart.md scenarios A–F (needs a real AWS account).

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- IaC: `infra/aws/` (bootstrap state under `infra/aws/bootstrap/`)
- Operator CLI: `scripts/cloud/`
- Remote instance scripts: `scripts/cloud/remote/`
- Tests: `tests/contract/`, `tests/integration/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create the repository structure and shared config the cloud lifecycle is built on.

- [ ] T001 Create the directory structure per plan.md: `infra/aws/bootstrap/` and `scripts/cloud/remote/`
- [ ] T002 [P] Update `.gitignore`: add `.terraform/`, `*.tfstate`, `*.tfstate.*` (keep `.terraform.lock.hcl`), and the exception `!.env.cloud.example` (current `!.env.example` already whitelists the base example)
- [ ] T003 [P] Create `docker-compose.cloud.yml`: a cloud override running `scheduler` and `ui` services from the existing image, exposing the UI on port 9000, with the data volume mounted at `/app/data`
- [ ] T004 [P] Create `.env.cloud.example` documenting cloud-relevant env keys (scope, politeness, scheduler hour/minute, SMTP/ALERT_EMAIL for digests) with no secret values
- [ ] T005 [P] Implement `scripts/cloud/check.sh`: run `tofu fmt -check -recursive infra/aws`, `tofu validate` on the bootstrap and main configs, and `sh -n` over `scripts/cloud/*.sh`; exit non-zero on any failure

**Checkpoint**: Directory structure and static-validation tooling exist; no user story can start before these.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The two retained resources (S3 state bucket + EBS data volume) and the shared pieces every story depends on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T006 Create `infra/aws/bootstrap/main.tf`: `aws_s3_bucket` (versioning + SSE) for Terraform state and `aws_ebs_volume` (gp3, size from a variable, tags `Name=property-hunter-data`) with `lifecycle { prevent_destroy = true }`
- [ ] T007 Create `infra/aws/bootstrap/outputs.tf` (bucket name, volume id, availability_zone) and the bootstrap config's local backend (`infra/aws/bootstrap/terraform.tfstate` stays untracked via T002)
- [ ] T008 Create `infra/aws/backend.tf`: S3 backend for the main stack (`bucket` from a variable, `key`, `region`, `use_lockfile = true`)
- [ ] T009 Create `infra/aws/variables.tf`: `region`, `az`, `state_bucket`, `instance_type` (default `t4g.small`), `app_ref` (default `HEAD`), `volume_device` (default `/dev/sdf`)
- [ ] T010 Create `infra/aws/data.tf`: `data "aws_ebs_volume" "data"` filtered by `tag:Name = property-hunter-data` (volume is a data source here, never a managed resource — `destroy` must not touch it, per research §4)
- [ ] T011 Implement `scripts/cloud/bootstrap.sh`: idempotently `tofu init && tofu apply` the bootstrap config (given `--bucket`, `--az`, `--volume-size`), print bucket/volume/az; JSON-capable per `contracts/cloud-cli-v1.md`
- [ ] T012 Implement `scripts/cloud/secrets.sh`: `push`/`pull`/`list` subcommands against SSM Parameter Store path `/property-hunter/*` as `SecureString`; refuse overwriting an existing `.env` without `--force`
- [ ] T013 Implement `scripts/cloud/remote/remote-deploy.sh`: install Docker + compose plugin if absent; format/mount the EBS volume by label/UUID at `/opt/property-hunter/data`; fetch secrets via `aws ssm get-parameters-by-path --path /property-hunter --with-decryption` into the app `.env`; `docker compose -f docker-compose.cloud.yml up -d --build` (idempotent)

**Checkpoint**: Foundation ready — a bootstrap run yields a state bucket + tagged volume; secrets can be pushed/pulled; `remote-deploy.sh` is installable via SSM. User story implementation can now begin.

---

## Phase 3: User Story 1 - Provision + Deploy in One Step (Priority: P1) 🎯 MVP

**Goal**: One `up.sh` command provisions a fresh environment and deploys the running app (scheduler + dashboard), reachable via an SSM tunnel.

**Independent Test**: From an account with only the bootstrap resources, run `./scripts/cloud/up.sh --bucket <name> --auto-approve`; `dashboard.sh` reaches the UI at `http://localhost:9000`; a scheduled run fires within 24 h and its results appear.

### Implementation for User Story 1

- [ ] T014 [US1] Create network resources in `infra/aws/main.tf`: VPC (small CIDR, DNS enabled), one public subnet pinned to `var.az`, internet gateway + route table/association, security group with **no inbound rules** (SSM needs none) and default egress
- [ ] T015 [US1] Create identity + bootstrapping in `infra/aws/main.tf`: IAM role + instance profile with `AmazonSSMManagedInstanceCore`, and `aws_instance.user_data` that calls `scripts/cloud/remote/remote-deploy.sh`
- [ ] T016 [US1] Create the compute + data wiring in `infra/aws/main.tf`: `aws_instance` (no `key_name`, `iam_instance_profile`, `user_data`, `instance_type` from var) and `aws_volume_attachment` (volume id from `data.aws_ebs_volume.data`, fixed `device_name` from `var.volume_device`)
- [ ] T017 [US1] Create `infra/aws/outputs.tf`: instance id, instance type, deployed ref, and the dashboard tunnel command hint
- [ ] T018 [US1] Implement `scripts/cloud/deploy.sh`: `aws ssm send-command` (document `AWS-RunShellScript`) to run `remote-deploy.sh` at `--ref <ref>` on the instance; used for initial deploy and mid-cycle rollback
- [ ] T019 [US1] Implement `scripts/cloud/up.sh`: guarded stale-lock check (`tofu force-unlock` only when a lock exists); `tofu plan` with a safety gate that aborts (exit 1) if the plan would create or destroy the retained resources; `tofu apply` (confirm unless `--auto-approve`); wait for SSM registration; invoke `deploy.sh`; wait for the dashboard health check; print `dashboard.sh` instructions; `--json` per `contracts/status-output-v1.schema.json`
- [ ] T020 [US1] Implement `scripts/cloud/dashboard.sh`: `aws ssm start-session` port-forward (document `AWS-StartPortForwardingSession`, instance port 9000 → `localhost:--local-port`), print `http://localhost:<local-port>`, block while the tunnel is open

**Checkpoint**: User Story 1 is fully functional — `up.sh` provisions + deploys and `dashboard.sh` reaches the UI. Independently testable via quickstart Scenario A.

---

## Phase 4: User Story 2 - Deprovision in One Step (Priority: P2)

**Goal**: One `down.sh` command stops the app and destroys every environment resource, leaving only the retained bucket + volume.

**Independent Test**: After a US1 environment is running, run `./scripts/cloud/down.sh --yes`; `status.sh --json` reports `environment: null`, and a console resource list shows no project compute/network resources.

### Implementation for User Story 2

- [ ] T021 [US2] Implement `scripts/cloud/down.sh`: SSM `send-command` a graceful `docker compose down`; `tofu plan -destroy` with a safety gate aborting (exit 1) if the plan would destroy the retained resources; `tofu destroy` (confirm unless `--yes`); print the retained bucket/volume and confirm data is preserved; `--json` per the status schema
- [ ] T022 [US2] Implement `scripts/cloud/status.sh`: read the OpenTofu state + AWS inventory, report instance state/ref, retained resources, and the estimated monthly cost from `research.md` §10 vs the USD 5 budget; `--json` MUST validate against `contracts/status-output-v1.schema.json`
- [ ] T023 [US2] Validate `down.sh` against the `contracts/cloud-cli-v1.md` contract: exit codes (0 success, 1 refused destroy, 2 tooling error), `--help`, and `--yes` confirmation behavior

**Checkpoint**: User Stories 1 and 2 both work independently — provision+deploy and full deprovision.

---

## Phase 5: User Story 3 - Weekly Cycles Without Data Loss (Priority: P3)

**Goal**: Repeated provision/deprovision cycles per week are cheap, idempotent, and preserve 100% of collected data on the retained volume.

**Independent Test**: up → collect → down → up again; the dashboard still shows the previously collected listings/run history; `status.sh` stays under the USD 5 budget.

### Implementation for User Story 3

- [ ] T024 [US3] Add `--wipe-data` to `scripts/cloud/down.sh`: an explicit, double-confirmed option that ALSO destroys the retained volume for a truly fresh start; default behavior keeps data (never implicit)
- [ ] T025 [US3] Make `up.sh`/`down.sh` idempotent no-ops: `up.sh` when already deployed exits 0 with no plan changes; `down.sh` with nothing provisioned exits 0 cleanly
- [ ] T026 [US3] Harden reattach robustness in `scripts/cloud/remote/remote-deploy.sh`: mount the EBS volume by filesystem label/UUID (not device name), so device renames never break a reattach after `down`/`up`
- [ ] T027 [US3] Validate the full cycle per `quickstart.md` scenarios C–F (data survival, cost visibility, rollback, idempotence) and fix any gaps found

**Checkpoint**: All user stories are independently functional and the weekly cycle is validated end-to-end.

---

## Phase N: Polish & Cross-Cutting Concerns

**Purpose**: Tests, docs, CI, and final validation affecting the whole feature.

- [ ] T028 [P] Add contract test `tests/contract/test_cloud_status.py`: assert `status.sh --json` output (fixture) validates against `contracts/status-output-v1.schema.json`
- [ ] T029 [P] Add `tests/integration/test_cloud_infra.py`: run `scripts/cloud/check.sh` (tofu validate/fmt, shell syntax) — skipped if OpenTofu/`sh` unavailable
- [ ] T030 [P] Add a CI job (`.github/workflows/ci.yml` if none exists) running `scripts/cloud/check.sh` on push/PR
- [ ] T031 Update `README.md` with a "Deploy to AWS" section: prerequisites, the one-time bootstrap + secrets push, `up`/`down`/`status`/`dashboard` usage, budget note, and links to `specs/002-cloud-provision-deploy/quickstart.md`
- [ ] T032 Final gate: run the full `quickstart.md` scenarios A–F against a real AWS account, confirm the constitution check (secrets never committed, retained data intact, polite collection config unchanged), and fix any gaps

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup; **BLOCKS all user stories** (bootstrap resources are referenced everywhere)
- **User Stories (Phase 3+)**: All depend on Foundational
  - US1 (P1) first; US2 (P2) next; US3 (P3) last — sequential, since US2/UP3 build on and validate US1's scripts
- **Polish (Final Phase)**: Depends on the user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Depends on Foundational (T006–T013). No dependency on US2/US3.
- **User Story 2 (P2)**: Depends on Foundational and US1 (it destroys what US1 creates); independently testable once US1 exists.
- **User Story 3 (P3)**: Depends on US1 + US2 (it exercises repeated up/down cycles); validation-only plus two small `down.sh`/`remote-deploy.sh` additions.

### Within Each User Story

- Terraform resources before the scripts that drive them (e.g., T014–T017 before T018–T020).
- `deploy.sh` (T018) before `up.sh` (T019), which calls it.
- Contract/validation tasks (T023, T027) run after implementation in their story.

### Parallel Opportunities

- All Setup tasks T002–T005 marked [P] can run in parallel (distinct files).
- Within Foundational: T008/T009/T010 are distinct `infra/aws/*.tf` files but must agree on variable names — run T009 first or jointly.
- `infra/aws/main.tf` is one file, so T014–T016 are sequential edits to the same file; do not parallelize.
- T028, T029, T030 (Polish, all [P]) can run in parallel.

---

## Parallel Example: User Story 1

```bash
# Sequential (same file main.tf, then scripts):
Task: "T014–T016 Create network/identity/compute in infra/aws/main.tf"
Task: "T017 outputs.tf"
Task: "T018 deploy.sh → T019 up.sh → T020 dashboard.sh"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories)
3. Complete Phase 3: User Story 1 (`up.sh` + `dashboard.sh`)
4. **STOP and VALIDATE**: quickstart Scenario A — dashboard reachable, scheduler runs
5. Demo if ready

### Incremental Delivery

1. Setup + Foundational → one-time bootstrap + secrets path working
2. Add User Story 1 → provision+deploy (MVP)
3. Add User Story 2 → deprovision + status/cost visibility
4. Add User Story 3 → weekly cycles, `--wipe-data`, idempotence
5. Polish → contract/static tests, CI, README, full quickstart A–F

### Parallel Team Strategy

With multiple developers, after Foundational:
- Developer A: US1 (T014–T020)
- Developer B: awaits US1, then US2 (T021–T023)
- Developer C: Polish tasks T028–T030 (independent files)

---

## Notes

- [P] tasks = different files, no dependencies.
- [Story] label maps a task to its user story for traceability.
- Each user story is independently completable and testable via its quickstart scenario.
- Static validation (`check.sh`) is expected to pass before any user-story checkpoint.
- Live AWS lifecycle validation is manual (quickstart scenarios A–F); do not gate CI on it.
- Avoid: same-file conflicts (`infra/aws/main.tf` edits are sequential), committing state files or `.env`/secrets (T002 + constitution gate).
