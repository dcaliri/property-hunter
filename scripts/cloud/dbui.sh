#!/usr/bin/env bash
# dbui.sh — open the SQLite web UI through an SSM port-forward tunnel.
#
# sqlite-web is a raw database browser (read-only in prod) and must never be
# exposed publicly. Like the dashboard, it binds 127.0.0.1:8012 inside the
# instance and is reachable only through an authenticated SSM Session Manager
# tunnel (no public IP, no inbound rules). Blocks while the tunnel is open.
#
# The local side defaults to 8013 so it never collides with the local
# docker-compose sqlite-web port (8012). Use --local-port to override.

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# shellcheck source=scripts/cloud/lib.sh
. scripts/cloud/lib.sh

INSTANCE=""
LOCAL_PORT=8013
REGION=""

usage() {
  cat <<'EOF'
Usage: scripts/cloud/dbui.sh [options]

Open an SSM port-forward tunnel to the sqlite-web UI. Blocks until closed.

Options:
  --instance <id>   Target EC2 instance (default: the running property-hunter one)
  --local-port <p>  Local port for the tunnel (default: 8013)
  --region <region> AWS region (default: derived from the data volume's AZ)
  --help            Show this help and exit

Exit codes: 0 success (tunnel closed), 1 expected failure, 2 tooling error.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --instance) INSTANCE="${2:?--instance needs a value}"; shift 2 ;;
    --local-port) LOCAL_PORT="${2:?--local-port needs a value}"; shift 2 ;;
    --region) REGION="${2:?--region needs a value}"; shift 2 ;;
    --help) usage; exit 0 ;;
    *) fail "unknown option: $1 (try --help)" ;;
  esac
done

require aws "AWS CLI v2 is required"
require session-manager-plugin "the AWS Session Manager plugin is required (https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)"

[ -n "$INSTANCE" ] || INSTANCE="$(running_instance_id)"
[ -n "$INSTANCE" ] || fail "no running environment found — run up.sh first"
[ -n "$REGION" ] || {
  load_bootstrap_env
  [ -n "${AZ:-}" ] && REGION="$(region_from_az "$AZ")"
  [ -n "$REGION" ] || REGION="$(aws configure get region || echo us-east-1)"
}

echo "Opening SSM tunnel to instance $INSTANCE (instance:8012 -> localhost:$LOCAL_PORT)"
echo "DB UI (read-only): http://localhost:$LOCAL_PORT/"
echo "Press Ctrl-C to close the tunnel."

aws ssm start-session --target "$INSTANCE" --region "$REGION" \
  --document-name AWS-StartPortForwardingSession \
  --parameters "portNumber=8012,localPortNumber=$LOCAL_PORT"
