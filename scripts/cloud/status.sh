#!/usr/bin/env bash
# status.sh — environment inventory + estimated monthly cost (FR-008).
#
# Reports whether an environment is provisioned, the retained resources, and the
# estimated monthly cost against the USD 5 budget. JSON output validates against
# contracts/status-output-v1.schema.json.

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# shellcheck source=scripts/cloud/lib.sh
. scripts/cloud/lib.sh

JSON=0
BUCKET=""

usage() {
  cat <<'EOF'
Usage: scripts/cloud/status.sh [options]

Report the environment inventory and estimated monthly cost.

Options:
  --json            Emit a single JSON document (status-output-v1 schema)
  --bucket <name>   State bucket (default: discovered from the retained volume)
  --help            Show this help and exit

Exit codes: 0 success, 2 tooling/environment error.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --json) JSON=1; shift ;;
    --bucket) BUCKET="${2:?--bucket needs a value}"; shift 2 ;;
    --help) usage; exit 0 ;;
    *) fail "unknown option: $1 (try --help)" ;;
  esac
done

require aws "AWS CLI v2 is required"
require python3 "python3 is required"

# --- Retained resources -------------------------------------------------------
load_bootstrap_env
VOLUME_JSON="$(volume_by_tag)"
if [ "$VOLUME_JSON" = "null" ] || [ -z "$VOLUME_JSON" ]; then
  fail "retained volume (Name=$VOLUME_TAG_NAME) not found — run bootstrap.sh first"
fi
VOLUME_ID="$(printf '%s' "$VOLUME_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["VolumeId"])')"
VOLUME_SIZE="$(printf '%s' "$VOLUME_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Size"])')"
AZ="$(printf '%s' "$VOLUME_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["AvailabilityZone"])')"
[ -n "$BUCKET" ] || BUCKET="$(printf '%s' "$VOLUME_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("Tags") or {}).get("StateBucket",""))')"
REGION="$(region_from_az "$AZ")"
[ -n "$BUCKET" ] || BUCKET="${BUCKET:-unknown}"

# --- Running environment ------------------------------------------------------
ENV_INSTANCE_ID="$(running_instance_id)"
ENV_STATE=""
ENV_INSTANCE_TYPE=""
ENV_AZ=""
ENV_APP_REF=""
if [ -n "$ENV_INSTANCE_ID" ]; then
  inst="$(aws ec2 describe-instances --instance-ids "$ENV_INSTANCE_ID" \
    --query "Reservations[0].Instances[0]" --output json 2>/dev/null || echo null)"
  ENV_INSTANCE_TYPE="$(printf '%s' "$inst" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("InstanceType","")) if d else print("")' 2>/dev/null || true)"
  ENV_AZ="$(printf '%s' "$inst" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("Placement",{}).get("AvailabilityZone","")) if d else print("")' 2>/dev/null || true)"
  ENV_APP_REF="$(printf '%s' "$inst" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(next((t["Value"] for t in (d.get("Tags") or []) if t["Key"]=="Ref"), "")) if d else print("")' 2>/dev/null || true)"
  ENV_STATE="deployed"
fi

# --- Cost ---------------------------------------------------------------------
ENV_RETAINED_BUCKET="$BUCKET"
ENV_RETAINED_VOLUME="$VOLUME_ID"
ENV_RETAINED_SIZE="$VOLUME_SIZE"
ENV_VOLUME_SIZE="$VOLUME_SIZE"
ENV_PUBLIC_IP=0
[ -n "$ENV_INSTANCE_ID" ] && ENV_PUBLIC_IP=1
ENV_GENERATED_AT="$(now_iso)"
set_cost_env

if [ "$JSON" -eq 1 ]; then
  emit_status_json
  exit 0
fi

# --- Human-readable -----------------------------------------------------------
echo "Property Hunter — cloud environment"
echo "------------------------------------"
if [ -n "$ENV_INSTANCE_ID" ]; then
  echo "Environment      : RUNNING ($ENV_STATE)"
  echo "  instance id    : $ENV_INSTANCE_ID"
  echo "  instance type  : $ENV_INSTANCE_TYPE"
  echo "  availability   : $ENV_AZ"
  echo "  app ref        : $ENV_APP_REF"
  echo "  access         : ssm-tunnel (scripts/cloud/dashboard.sh)"
else
  echo "Environment      : deprovisioned"
fi
echo "Retained"
echo "  state bucket    : $BUCKET"
echo "  data volume     : $VOLUME_ID (${VOLUME_SIZE} GiB, $AZ)"
echo "Cost"
echo "  estimated       : USD ${ENV_COST_ESTIMATED}/month"
echo "  budget          : USD ${ENV_COST_BUDGET}/month"
if [ "$ENV_COST_OVER" = "true" ]; then
  echo "  status          : OVER BUDGET"
else
  echo "  status          : within budget"
fi
echo "Assumptions:"
printf '%s\n' "$ENV_COST_ASSUMPTIONS" | sed 's/^/  - /'
