#!/usr/bin/env bash
# deploy.sh — deploy (or rollback) the app on a running environment.
#
# Builds + pushes the app image for a git ref to ECR, then runs the on-instance
# deploy script (remote-deploy.sh) over SSM send-command. No re-provisioning and
# no SSH. Used by up.sh for the initial deploy and directly for mid-cycle
# rollback (FR-010 / research §9).

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# shellcheck source=scripts/cloud/lib.sh
. scripts/cloud/lib.sh

REF=""
INSTANCE=""
REGION=""
NO_BUILD=0

usage() {
  cat <<'EOF'
Usage: scripts/cloud/deploy.sh --ref <ref> [options]

Deploy a specific git ref to the running environment over SSM.

Options:
  --ref <ref>        Git ref (branch, tag, or sha) to deploy (required)
  --instance <id>    Target EC2 instance (default: the running property-hunter one)
  --region <region>  AWS region (default: derived from the data volume's AZ)
  --no-build         Skip building/pushing the image (already pushed)
  --help             Show this help and exit

Exit codes: 0 success, 1 expected failure, 2 tooling/environment error.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --ref) REF="${2:?--ref needs a value}"; shift 2 ;;
    --instance) INSTANCE="${2:?--instance needs a value}"; shift 2 ;;
    --region) REGION="${2:?--region needs a value}"; shift 2 ;;
    --no-build) NO_BUILD=1; shift ;;
    --help) usage; exit 0 ;;
    *) fail "unknown option: $1 (try --help)" ;;
  esac
done

[ -n "$REF" ] || fail "--ref is required (try --help)"
require aws "AWS CLI v2 is required"
require docker "docker is required to build/push the app image"
require git "git is required to resolve --ref"

# --- Resolve target instance and region --------------------------------------
[ -n "$INSTANCE" ] || INSTANCE="$(running_instance_id)"
[ -n "$INSTANCE" ] || fail "no running environment found — run up.sh first (try --help)"
[ -n "$REGION" ] || {
  load_bootstrap_env
  if [ -n "${AZ:-}" ]; then REGION="$(region_from_az "$AZ")"; fi
  [ -n "$REGION" ] || REGION="$(aws configure get region || echo us-east-1)"
}

# --- Build + push the image for this ref (idempotent) ------------------------
sha="$(resolve_ref_sha "$REF")"
tag="$(resolve_ref_tag "$sha")"
registry="$(ecr_registry "$REGION")"
uri="$registry/property-hunter:$tag"
echo "==> Target image: $uri"

if [ "$NO_BUILD" -eq 0 ]; then
  build_dir="$PWD"
  current_sha="$(git rev-parse --short HEAD 2>/dev/null || true)"
  if [ "$current_sha" != "$sha" ]; then
    wt="$(mktemp -d)/ph-build"
    echo "==> Checking out $REF ($sha) in a detached worktree"
    git worktree add --detach "$wt" "$REF" >/dev/null || die "could not check out $REF"
    build_dir="$wt"
  fi

  platform="$(docker info --format '{{.Architecture}}' 2>/dev/null || echo unknown)"
  echo "==> Building image (host platform: $platform)"
  if [ "$platform" = "aarch64" ] || [ "$platform" = "arm64" ]; then
    docker build -t "$uri" "$build_dir"
  else
    echo "    note: forcing linux/arm64; amd64 hosts need buildx + QEMU"
    docker build --platform linux/arm64 -t "$uri" "$build_dir"
  fi

  echo "==> Pushing to ECR"
  aws ecr get-login-password --region "$REGION" \
    | docker login --username AWS --password-stdin "$registry" >/dev/null
  docker push "$uri" >/dev/null

  if [ "$build_dir" != "$PWD" ]; then
    git worktree remove --force "$build_dir"
  fi
fi

# --- Run the on-instance deploy script over SSM ------------------------------
echo "==> Deploying to instance $INSTANCE over SSM"
# Ship the current remote-deploy.sh to the instance first so local fixes
# propagate without rebuilding the instance (user-data only runs at first boot).
script_b64="$(base64 < scripts/cloud/remote/remote-deploy.sh | tr -d '\n')"
push_cmd="printf '%s' '$script_b64' | base64 -d > /opt/property-hunter/remote-deploy.sh && chmod +x /opt/property-hunter/remote-deploy.sh"
push_id="$(ssm_run "$INSTANCE" "$REGION" "$push_cmd")" || die "failed to send script"
ssm_wait "$INSTANCE" "$REGION" "$push_id" 120 || fail "failed to upload remote-deploy.sh"

cmd="export APP_IMAGE='$uri' DATA_DIR='/opt/property-hunter/data' REGION='$REGION'; \
if [ -f /opt/property-hunter/remote-deploy.sh ]; then /opt/property-hunter/remote-deploy.sh; \
else echo 'ERROR: remote-deploy.sh missing — re-run up.sh to rebuild the instance' >&2; exit 1; fi"

cmd_id="$(ssm_run "$INSTANCE" "$REGION" "$cmd")" || die "failed to send SSM command"
ssm_wait "$INSTANCE" "$REGION" "$cmd_id" 900 || fail "deploy failed on the instance"
echo "==> Deployed $REF ($sha) as $uri"
