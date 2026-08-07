#!/usr/bin/env bash
# down.sh — deprovision the entire environment in one step (US2).
#
# Stops the app gracefully, destroys every ephemeral environment resource, and
# leaves the retained bucket + volume (and ECR repo) intact. Add --wipe-data to
# ALSO destroy the retained volume for a truly fresh start (US3, explicit only).

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# shellcheck source=scripts/cloud/lib.sh
. scripts/cloud/lib.sh

BUCKET=""
YES=0
JSON=0
WIPE_DATA=0
PLAN_FILE="$ROOT/infra/aws/.destroy.plan"

usage() {
  cat <<'EOF'
Usage: scripts/cloud/down.sh [options]

Deprovision the environment: stop the app, destroy every ephemeral resource,
keep the retained state bucket + EBS volume + ECR repo.

Options:
  --bucket <name>   State bucket from bootstrap.sh (optional; from .bootstrap.env)
  --yes             Destroy without prompting
  --json            Emit a single JSON document (status-output-v1)
  --wipe-data       ALSO destroy the retained EBS volume (double-confirmed).
                    Never done implicitly — routine deprovision keeps data.
  --help            Show this help and exit

Exit codes: 0 success (incl. nothing provisioned), 1 refused / not clean,
           2 tooling/environment error.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --bucket) BUCKET="${2:?--bucket needs a value}"; shift 2 ;;
    --yes) YES=1; shift ;;
    --json) JSON=1; shift ;;
    --wipe-data) WIPE_DATA=1; shift ;;
    --help) usage; exit 0 ;;
    *) fail "unknown option: $1 (try --help)" ;;
  esac
done

require aws "AWS CLI v2 is required"
require python3 "python3 is required"
TF="$(find_tf)"

# --- Locate the retained resources -------------------------------------------
load_bootstrap_env
[ -n "$BUCKET" ] || BUCKET="${BUCKET:-}"
VOLUME_JSON="$(volume_by_tag)"
[ "$VOLUME_JSON" != "null" ] || fail "retained volume (Name=$VOLUME_TAG_NAME) not found — nothing was provisioned via this tool?"
VOLUME_ID="$(printf '%s' "$VOLUME_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("VolumeId",""))' 2>/dev/null || true)"
VOLUME_SIZE="$(printf '%s' "$VOLUME_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("Size",20))' 2>/dev/null || true)"
AZ="$(printf '%s' "$VOLUME_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("AvailabilityZone",""))' 2>/dev/null || true)"
REGION="$(region_from_az "$AZ")"

if [ -z "$BUCKET" ]; then
  BUCKET="$(printf '%s' "$VOLUME_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("Tags") or {}).get("StateBucket",""))' 2>/dev/null || true)"
fi
[ -n "$BUCKET" ] || fail "could not determine the state bucket (pass --bucket)"

# --- Is anything provisioned? (idempotent no-op when clean, T025) -------------
"$TF" -chdir=infra/aws init -reconfigure -backend-config="bucket=$BUCKET" -backend-config="region=$REGION" >/dev/null \
  || die "terraform init failed"

if ! "$TF" -chdir=infra/aws state list 2>/dev/null | grep -q 'aws_instance'; then
  echo "==> Nothing provisioned — nothing to do (idempotent no-op). Retained resources intact."
  echo "    bucket: $BUCKET | volume: $VOLUME_ID (${VOLUME_SIZE} GiB, $AZ)"
  ENV_STATE="" ; ENV_RETAINED_BUCKET="$BUCKET" ; ENV_RETAINED_VOLUME="$VOLUME_ID" ; ENV_RETAINED_SIZE="$VOLUME_SIZE"
  ENV_GENERATED_AT="$(now_iso)" ; ENV_PUBLIC_IP=0 ; set_cost_env
  [ "$JSON" -eq 1 ] && emit_status_json
  exit 0
fi

# --- Graceful stop of the app (best-effort; instance may be unreachable) ------
INSTANCE_ID="$(running_instance_id)"
if [ -n "$INSTANCE_ID" ]; then
  echo "==> Stopping the app on instance $INSTANCE_ID"
  cmd="cd /opt/property-hunter 2>/dev/null || exit 0; \
docker compose -f docker-compose.cloud.yml down 2>/dev/null || true"
  cid="$(ssm_run "$INSTANCE_ID" "$REGION" "$cmd" 2>/dev/null || true)"
  [ -n "$cid" ] && ssm_wait "$INSTANCE_ID" "$REGION" "$cid" 300 >/dev/null 2>&1 || true
fi

# --- Plan the destroy + safety gate + confirmation ----------------------------
echo "==> Planning destroy"
"$TF" -chdir=infra/aws plan -destroy -input=false -out="$PLAN_FILE" \
  -var "region=$REGION" -var "az=$AZ" -var "state_bucket=$BUCKET" \
  || die "terraform plan -destroy failed"

gate_plan "$PLAN_FILE" || fail "aborting: destroy plan touches a retained resource"
"$TF" -chdir=infra/aws show "$PLAN_FILE" >/dev/null 2>&1 || true

if [ "$YES" -eq 0 ]; then
  echo ""
  echo "This will destroy every ephemeral environment resource."
  printf 'Continue? [y/N] '
  read -r y
  [ "$y" = "y" ] || fail "aborted (nothing destroyed)"
fi

echo "==> Destroying"
"$TF" -chdir=infra/aws apply "$PLAN_FILE" || die "terraform destroy failed"
rm -f "$PLAN_FILE"

# --- Optional wipe of the retained volume (explicit, double-confirmed) --------
if [ "$WIPE_DATA" -eq 1 ]; then
  echo ""
  echo "WARNING: --wipe-data destroys the retained EBS volume ($VOLUME_ID)."
  printf 'ALL collected data will be lost. Type DELETE to confirm: '
  read -r confirm
  [ "$confirm" = "DELETE" ] || fail "wipe aborted (data preserved)"
  echo "==> Deleting volume $VOLUME_ID"
  aws ec2 delete-volume --volume-id "$VOLUME_ID" --region "$REGION" || fail "volume deletion failed"
  "$TF" -chdir=infra/aws/bootstrap init -reconfigure >/dev/null 2>&1 || true
  "$TF" -chdir=infra/aws/bootstrap state rm aws_ebs_volume.data >/dev/null 2>&1 \
    || echo "  (note: volume not in bootstrap state — re-run bootstrap.sh)"
  echo "==> Volume deleted. Re-run bootstrap.sh to create a fresh one."
fi

echo "==> Environment deprovisioned."
echo "    Retained (never removed):"
echo "      state bucket : $BUCKET"
echo "      data volume  : $VOLUME_ID (${VOLUME_SIZE} GiB, $AZ)"
echo "      ECR repo     : kept"
[ "$WIPE_DATA" -eq 0 ] && echo "    Data is preserved on the volume — a later up.sh reattaches it."

ENV_STATE=""
ENV_RETAINED_BUCKET="$BUCKET"
ENV_RETAINED_VOLUME="$VOLUME_ID"
ENV_RETAINED_SIZE="$VOLUME_SIZE"
ENV_PUBLIC_IP=0
ENV_GENERATED_AT="$(now_iso)"
set_cost_env
[ "$JSON" -eq 1 ] && emit_status_json
