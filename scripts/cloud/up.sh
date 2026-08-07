#!/usr/bin/env bash
# up.sh — provision + deploy the full environment in one step (US1).
#
# Builds and pushes the app image for a git ref, applies the ephemeral main
# stack (VPC, subnet, IAM, instance + retained-volume attach), waits for SSM
# registration, deploys via deploy.sh, and confirms the dashboard is healthy.

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# shellcheck source=scripts/cloud/lib.sh
. scripts/cloud/lib.sh

BUCKET=""
REF="HEAD"
INSTANCE_TYPE="t4g.small"
AUTO_APPROVE=0
JSON=0
PLAN_FILE="$ROOT/infra/aws/.plan.tfplan"

usage() {
  cat <<'EOF'
Usage: scripts/cloud/up.sh --bucket <name> [options]

Provision a fresh environment and deploy the app (scheduler + dashboard) in one
step. Re-running while already deployed is an idempotent no-op.

Options:
  --bucket <name>      State bucket from bootstrap.sh (required)
  --ref <ref>          Git ref to deploy (default: HEAD)
  --instance-type <t>  EC2 instance type (default: t4g.small)
  --auto-approve       Apply without prompting
  --json               Emit a single JSON document (status-output-v1)
  --help               Show this help and exit

Exit codes: 0 success, 1 expected failure (plan not clean / refused), 2 tooling.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --bucket) BUCKET="${2:?--bucket needs a value}"; shift 2 ;;
    --ref) REF="${2:?--ref needs a value}"; shift 2 ;;
    --instance-type) INSTANCE_TYPE="${2:?--instance-type needs a value}"; shift 2 ;;
    --auto-approve) AUTO_APPROVE=1; shift ;;
    --json) JSON=1; shift ;;
    --help) usage; exit 0 ;;
    *) fail "unknown option: $1 (try --help)" ;;
  esac
done

[ -n "$BUCKET" ] || fail "--bucket is required (try --help)"
require aws "AWS CLI v2 is required"
require python3 "python3 is required"
require docker "docker is required to build/push the app image"
require git "git is required to resolve --ref"
TF="$(find_tf)"

# --- Derive region/AZ from the retained volume -------------------------------
load_bootstrap_env
VOLUME_JSON="$(volume_by_tag)"
AZ="$(printf '%s' "$VOLUME_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("AvailabilityZone","") if d else "")' 2>/dev/null || true)"
[ -n "$AZ" ] || fail "retained volume (Name=$VOLUME_TAG_NAME) not found — run bootstrap.sh first"
REGION="$(region_from_az "$AZ")"
VOLUME_ID="$(printf '%s' "$VOLUME_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("VolumeId","") if d else "")' 2>/dev/null || true)"
VOLUME_SIZE="$(printf '%s' "$VOLUME_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("Size",20) if d else 20)' 2>/dev/null || true)"
echo "==> Region: $REGION | AZ: $AZ | volume: $VOLUME_ID (${VOLUME_SIZE} GiB)"

# --- Build + push the image for the ref --------------------------------------
sha="$(resolve_ref_sha "$REF")"
tag="$(resolve_ref_tag "$sha")"
registry="$(ecr_registry "$REGION")"
uri="$registry/property-hunter:$tag"

build_dir="$PWD"
current_sha="$(git rev-parse --short HEAD 2>/dev/null || true)"
if [ "$current_sha" != "$sha" ]; then
  wt="$(mktemp -d)/ph-build"
  echo "==> Checking out $REF ($sha) in a detached worktree"
  git worktree add --detach "$wt" "$REF" >/dev/null || die "could not check out $REF"
  build_dir="$wt"
fi
platform="$(docker info --format '{{.Architecture}}' 2>/dev/null || echo unknown)"
echo "==> Building image $uri (host platform: $platform)"
if [ "$platform" = "aarch64" ] || [ "$platform" = "arm64" ]; then
  docker build -t "$uri" "$build_dir"
else
  echo "    note: forcing linux/arm64; amd64 hosts need buildx + QEMU"
  docker build --platform linux/arm64 -t "$uri" "$build_dir"
fi
echo "==> Pushing to ECR"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$registry" >/dev/null
docker push "$uri" >/dev/null
if [ "$build_dir" != "$PWD" ]; then git worktree remove --force "$build_dir"; fi

# --- Terraform: init, plan, safety gate, apply --------------------------------
echo "==> Initializing main stack state (backend: s3://$BUCKET)"
"$TF" -chdir=infra/aws init -reconfigure -backend-config="bucket=$BUCKET" -backend-config="region=$REGION" >/dev/null \
  || die "terraform init failed"

# Guarded stale-lock check: only unlock when a lock object actually exists.
LOCK_FILE="$(mktemp -t ph-lock.XXXXXX)"
if aws s3api head-object --bucket "$BUCKET" --key "property-hunter/main.tfstate.tflock" >/dev/null 2>&1; then
  lock_id=""
  if aws s3api get-object --bucket "$BUCKET" --key "property-hunter/main.tfstate.tflock" "$LOCK_FILE" >/dev/null 2>&1; then
    lock_id="$(python3 -c "import json; print(json.load(open('$LOCK_FILE')).get('ID',''))" 2>/dev/null || true)"
  fi
  if [ -n "$lock_id" ]; then
    echo "==> Stale state lock detected ($lock_id); force-unlocking"
    [ "$AUTO_APPROVE" -eq 0 ] && { printf 'Force-unlock now? [y/N] '; read -r y; [ "$y" = "y" ] || fail "aborted"; }
    "$TF" -chdir=infra/aws force-unlock -force "$lock_id" >/dev/null || die "force-unlock failed"
  else
    echo "==> Stale lock object present but unreadable; leaving it (investigate manually)"
  fi
fi
rm -f "$LOCK_FILE"

echo "==> Planning (ref=$REF, instance_type=$INSTANCE_TYPE)"
"$TF" -chdir=infra/aws plan -input=false -out="$PLAN_FILE" \
  -var "region=$REGION" -var "az=$AZ" -var "state_bucket=$BUCKET" \
  -var "instance_type=$INSTANCE_TYPE" -var "app_ref=$REF" -var "app_image=$uri" \
  || die "terraform plan failed"

gate_plan "$PLAN_FILE" || fail "aborting: plan is not clean"
if "$TF" -chdir=infra/aws show "$PLAN_FILE" 2>/dev/null | grep -q "No changes"; then
  echo "==> Nothing to provision — environment already up (idempotent no-op)"
else
  if [ "$AUTO_APPROVE" -eq 0 ]; then
    printf 'Apply this plan? [y/N] '
    read -r y
    [ "$y" = "y" ] || fail "aborted (no changes applied)"
  fi
  echo "==> Applying"
  "$TF" -chdir=infra/aws apply "$PLAN_FILE" || die "terraform apply failed"
fi
rm -f "$PLAN_FILE"

INSTANCE_ID="$("$TF" -chdir=infra/aws output -raw instance_id)" || die "missing instance_id output"
INSTANCE_TYPE_ACT="$("$TF" -chdir=infra/aws output -raw instance_type || true)"

# --- Wait for SSM, then deploy ------------------------------------------------
echo "==> Waiting for instance $INSTANCE_ID to register with SSM"
ssm_wait_online "$INSTANCE_ID" "$REGION" 900 || fail "instance did not register with SSM"

echo "==> Deploying $REF"
scripts/cloud/deploy.sh --instance "$INSTANCE_ID" --ref "$REF" --region "$REGION" --no-build \
  || fail "deploy failed"
echo "==> Environment is up. Dashboard:"
echo "    scripts/cloud/dashboard.sh --instance $INSTANCE_ID"

# --- Report (human or JSON) ---------------------------------------------------
ENV_STATE="deployed"
ENV_INSTANCE_ID="$INSTANCE_ID"
ENV_INSTANCE_TYPE="${INSTANCE_TYPE_ACT:-$INSTANCE_TYPE}"
ENV_AZ="$AZ"
ENV_APP_REF="$REF"
ENV_RETAINED_BUCKET="$BUCKET"
ENV_RETAINED_VOLUME="$VOLUME_ID"
ENV_RETAINED_SIZE="$VOLUME_SIZE"
ENV_PUBLIC_IP=1
ENV_GENERATED_AT="$(now_iso)"
set_cost_env

if [ "$JSON" -eq 1 ]; then
  emit_status_json
else
  echo ""
  echo "Environment summary:"
  echo "  instance id   : $INSTANCE_ID"
  echo "  instance type : ${INSTANCE_TYPE_ACT:-$INSTANCE_TYPE}"
  echo "  deployed ref  : $REF ($sha)"
  echo "  image         : $uri"
  echo "  dashboard     : scripts/cloud/dashboard.sh --instance $INSTANCE_ID"
  echo "  retained      : bucket=$BUCKET volume=$VOLUME_ID"
  echo "  est. cost     : USD ${ENV_COST_ESTIMATED}/mo (budget USD $ENV_COST_BUDGET)"
fi
