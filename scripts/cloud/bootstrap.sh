#!/usr/bin/env bash
# bootstrap.sh — one-time AWS setup (run once per account).
#
# Creates the retained resources that `down.sh` never removes:
#   - S3 state bucket (versioned, SSE) for the main stack's Terraform state
#   - EBS gp3 data volume (tagged property-hunter-data)
#   - ECR repository holding deployed app images
#
# Writes infra/aws/.bootstrap.env so later scripts know the retained resources.

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# shellcheck source=scripts/cloud/lib.sh
. scripts/cloud/lib.sh

STATE_DIR="${STATE_DIR:-infra/aws/bootstrap}"
BUCKET=""
AZ=""
VOLUME_SIZE=20
JSON=0

usage() {
  cat <<'EOF'
Usage: scripts/cloud/bootstrap.sh --bucket <name> --az <zone> [options]

One-time AWS setup for Property Hunter. Creates the retained resources that
`down.sh` never removes: the S3 state bucket, the EBS data volume, and the ECR
repository.

Options:
  --bucket <name>        Globally unique S3 bucket name for state (required)
  --az <zone>            Availability zone, e.g. us-east-1a (required)
  --volume-size <gb>     EBS gp3 size in GiB (default: 20)
  --state-dir <path>     Where the bootstrap state lives (default: infra/aws/bootstrap)
  --json                 Emit a single JSON document on stdout
  --help                 Show this help and exit

Exit codes: 0 success, 1 expected failure, 2 tooling/environment error.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --bucket) BUCKET="${2:?--bucket needs a value}"; shift 2 ;;
    --az) AZ="${2:?--az needs a value}"; shift 2 ;;
    --volume-size) VOLUME_SIZE="${2:?--volume-size needs a value}"; shift 2 ;;
    --state-dir) STATE_DIR="${2:?--state-dir needs a value}"; shift 2 ;;
    --json) JSON=1; shift ;;
    --help) usage; exit 0 ;;
    *) fail "unknown option: $1 (try --help)" ;;
  esac
done

[ -n "$BUCKET" ] || fail "--bucket is required (try --help)"
[ -n "$AZ" ] || fail "--az is required (try --help)"
case "$VOLUME_SIZE" in
  ''|*[!0-9]*) fail "--volume-size must be a positive integer (got '$VOLUME_SIZE')" ;;
esac

require aws "AWS CLI v2 is required"
require python3 "python3 is required for JSON output"
TF="$(find_tf)"

REGION="$(region_from_az "$AZ")"
if [ -z "$REGION" ]; then
  fail "could not derive a region from AZ '$AZ'"
fi

echo "==> Bootstrapping retained resources in $REGION (bucket=$BUCKET, az=$AZ, volume=${VOLUME_SIZE}GiB)"

"$TF" -chdir="$STATE_DIR" init -reconfigure >/dev/null || die "terraform init failed"
"$TF" -chdir="$STATE_DIR" plan \
  -var "bucket=$BUCKET" -var "az=$AZ" -var "region=$REGION" -var "volume_size=$VOLUME_SIZE" \
  -input=false -out=.bootstrap.plan || die "terraform plan failed"

# Idempotent: a no-change plan exits 0 with "No changes".
"$TF" -chdir="$STATE_DIR" apply -auto-approve .bootstrap.plan || die "terraform apply failed"

VOLUME_ID="$("$TF" -chdir="$STATE_DIR" output -raw data_volume_id)" || die "missing data_volume_id output"
VOLUME_SIZE_ACT="$("$TF" -chdir="$STATE_DIR" output -raw data_volume_size_gb)" || true
ECR_URL="$("$TF" -chdir="$STATE_DIR" output -raw ecr_repository_url)" || true

# Remember the retained resources for up.sh/down.sh/status.sh.
mkdir -p "$(dirname "$BOOTSTRAP_ENV")"
cat > "$BOOTSTRAP_ENV" <<EOF
BUCKET=$BUCKET
VOLUME_ID=$VOLUME_ID
VOLUME_SIZE=$VOLUME_SIZE_ACT
AZ=$AZ
REGION=$REGION
ECR_REPO=$ECR_URL
EOF

echo "==> Retained resources ready:"
echo "    state bucket : $BUCKET"
echo "    data volume  : $VOLUME_ID (${VOLUME_SIZE_ACT} GiB, $AZ)"
echo "    ECR repo     : $ECR_URL"
echo "    saved to     : $BOOTSTRAP_ENV"

if [ "$JSON" -eq 1 ]; then
  python3 - "$BUCKET" "$VOLUME_ID" "$VOLUME_SIZE_ACT" "$AZ" <<'PY'
import json, os, sys
bucket, volume, size, az = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
print(json.dumps({
    "tool": "property-hunter-cloud",
    "version": "v1",
    "generated_at": os.popen("date -u +%Y-%m-%dT%H:%M:%SZ").read().strip(),
    "bootstrap": {
        "state_bucket": bucket,
        "data_volume_id": volume,
        "data_volume_size_gb": int(size),
        "availability_zone": az,
    },
}, indent=2))
PY
fi
