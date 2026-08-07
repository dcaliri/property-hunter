# Feature Specification: Cloud Provision & Deploy

**Feature Branch**: `002-cloud-provision-deploy`

**Created**: 2026-08-07

**Status**: Draft

**Input**: User description: "I want a mechanism that allows easy provision and deploy of this solution to the cloud, as well as easy deprovision so it can be done multiple times per week easily. It needs to be something cheap and we should leverage IaC. AWS is preferable"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Provision and deploy a running environment in one step (Priority: P1)

The operator wants to run the full Property Hunter solution in the cloud: the daily scheduled collection job, the read-only dashboard, and the persistent database. Starting from a clean state (nothing provisioned), one action creates every needed resource and deploys the current application, after which the dashboard is reachable and the scheduler begins running on its daily schedule.

**Why this priority**: This is the core promise of the feature — "easy provision and deploy". Without it there is no value.

**Independent Test**: From an account with zero project resources, run the provision-and-deploy step; verify the dashboard loads at its address and that a scheduled pipeline run executes within the first 24 hours and its results are stored.

**Acceptance Scenarios**:

1. **Given** a clean cloud account with no project resources, **When** the operator runs the single provision-and-deploy step, **Then** all required infrastructure is created and the application is running.
2. **Given** the application is deployed, **When** the operator opens the dashboard address, **Then** the dashboard renders the current listing, run, and detection data.
3. **Given** a deployed environment, **When** the configured daily schedule time arrives, **Then** a full pipeline run executes automatically and its results are persisted.

---

### User Story 2 - Deprovision the entire environment in one step (Priority: P2)

The operator can tear down everything with one action: all billable resources created for the environment are removed, nothing is left behind, and no manual cleanup in the cloud console is required.

**Why this priority**: "Easy deprovision" is the other half of the requirement and directly controls cost, so it must exist for the feature to be usable weekly.

**Independent Test**: After a period of normal running, execute the deprovision step; verify the account's project resource list is empty and that subsequent monthly billing drops to near zero.

**Acceptance Scenarios**:

1. **Given** a running environment, **When** the operator runs the deprovision step, **Then** every project resource is removed.
2. **Given** resources have been removed, **When** the operator inspects the account, **Then** no project resources remain (retained persistent state storage, if enabled, is the only exception).
3. **Given** a deprovision is triggered while a scheduled run is executing, **When** the process completes, **Then** the persisted data is not corrupted and a later re-provision recovers cleanly.

---

### User Story 3 - Run multiple provision/deprovision cycles per week without losing data (Priority: P3)

The operator repeats the full cycle several times a week (for example: provision, collect fresh listings for a few days, deprovision, then bring it back up). Previously collected data — listings, run history, predictions — and configuration survive each cycle, and every cycle is fast and cheap enough to do casually.

**Why this priority**: This exercises the "multiple times per week easily" goal and the "cheap" constraint end-to-end.

**Independent Test**: Provision → collect data → deprovision → provision again; verify the previously collected listings and run history are present after the re-provision.

**Acceptance Scenarios**:

1. **Given** a previous environment that collected data, **When** the operator reprovisions, **Then** the previously collected data is available in the dashboard.
2. **Given** repeated weekly cycles, **When** the operator runs them, **Then** no manual configuration or console steps are required between cycles.
3. **Given** an environment left running for a full week, **When** the operator reviews cost, **Then** the cost is within the stated budget.

---

### Edge Cases

- Deprovision is requested while a scheduled pipeline run or background job is in progress → the data remains intact and re-provisioning is clean; no manual repair.
- Provisioning fails partway (resource limit, transient outage, misconfiguration) → re-running the provision step completes the environment without orphaned or duplicated resources.
- The operator wants a truly fresh start (no historical data) → an explicit wipe is available as an option; it is never done implicitly by a routine deprovision.
- The environment is left running longer than intended → the estimated monthly cost is visible before provisioning and the resource inventory is reported on deprovision, so cost stays predictable.
- A credential expires or must be rotated → it can be updated without a full redeploy, and it never appears in the repository.
- Cloud-account limits (quotas, capacity) block provisioning → the error is surfaced clearly with an actionable message.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a single action that provisions all infrastructure required to run the solution in the cloud.
- **FR-002**: System MUST provide a single action that deploys the current application onto provisioned infrastructure and starts the scheduled job and the dashboard.
- **FR-003**: System MUST provide a single action that deprovisions the environment by removing every billable resource it created.
- **FR-004**: All infrastructure MUST be defined declaratively as code, stored in the repository, so every environment is reproducible.
- **FR-005**: Provision and deprovision actions MUST be idempotent — safe to re-run without error or duplicate resources.
- **FR-006**: The application's persistent data MUST survive provision/deprovision cycles when state retention is enabled.
- **FR-007**: Secrets and credentials MUST be supplied through a managed secrets mechanism and MUST NEVER be stored in the repository.
- **FR-008**: The system MUST report the expected monthly cost before provisioning and the final resource inventory on deprovision.
- **FR-009**: A deployed environment MUST expose health status and logs so the operator can confirm it is working.
- **FR-010**: The operator MUST be able to redeploy a previous application version (rollback) without re-provisioning infrastructure.
- **FR-011**: Provision, deploy, and deprovision MUST be runnable entirely from a command line, with no cloud-console steps.
- **FR-012**: The solution MUST support the cloud provider the operator prefers (AWS) as its primary target.

### Key Entities *(include if feature involves data)*

- **Environment**: One deployed instance of the solution — its compute, storage, networking, and secrets — that can be created as a whole and removed as a whole.
- **Persistent State**: The collected data (listings, runs, predictions, notifications) that survives environments being replaced.
- **Deployment**: A specific application version running inside an environment; replaceable without touching infrastructure.
- **Secret**: A credential or configuration value required at runtime, stored outside the repository and injected at deploy time.
- **Provisioning Definition**: The declarative code that describes every resource of an environment.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A new environment is fully provisioned and the current application deployed from scratch in under 15 minutes.
- **SC-002**: Deprovisioning completes in under 10 minutes and leaves no billable project resources behind.
- **SC-003**: The operator can complete at least 3 full provision/deprovision cycles in a single week with no cloud-console actions.
- **SC-004**: A running environment's monthly cost stays under USD 5 excluding data transfers beyond normal use.
- **SC-005**: 100% of previously collected data survives a provision/deprovision cycle when state retention is enabled.
- **SC-006**: Provision, deploy, and deprovision each require at most 2 commands, from a clean clone of the repository.

## Assumptions

- The target cloud is AWS (operator preference); an AWS account with billing and basic IAM permissions already exists.
- Infrastructure is managed with declarative infrastructure-as-code; the specific tool (e.g., Terraform, CDK, CloudFormation) is chosen during planning.
- The cost model is a small always-on compute unit plus persistent storage; the daily scheduler workload is lightweight, so idle cost is the dominant factor.
- Persistent state is retained across cycles by default because storage is the least costly component; a fresh-start wipe is an explicit option, never implicit.
- The dashboard is reachable only while the environment is provisioned; it is not required to be always-on during teardown.
- Single operator; no multi-user access management, quotas, or billing-splitting is required.
- Deploying to regions beyond the single default region is out of scope for v1.
