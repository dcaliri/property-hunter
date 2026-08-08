#!/usr/bin/env bash
# dashboard.sh — open the dashboard through an SSM port-forward tunnel.
#
# The dashboard binds 127.0.0.1:9000 inside the instance and is reachable only
# through an authenticated SSM Session Manager tunnel (research §8: no public
# IP, no inbound rules, no Elastic IP). Blocks while the tunnel is open.
#
# The local side defaults to 9001 (not 9000) so the tunnel never collides with
# the cloud UI's own port or a local dev server. Use --local-port to override.

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# shellcheck source=scripts/cloud/lib.sh
. scripts/cloud/lib.sh

INSTANCE=""
LOCAL_PORT=9001
REGION=""

usage() {
  cat <<'EOF'
Usage: scripts/cloud/dashboard.sh [options]

Open an SSM port-forward tunnel to the running dashboard. Blocks until closed.

Options:
  --instance <id>   Target EC2 instance (default: the running property-hunter one)
  --local-port <p>  Local port for the tunnel (default: 9001)
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

echo "Opening SSM tunnel to instance $INSTANCE (instance:9000 -> localhost:$LOCAL_PORT)"
echo "Dashboard: http://localhost:$LOCAL_PORT/"
echo "Press Ctrl-C to close the tunnel."

aws ssm start-session --target "$INSTANCE" --region "$REGION" \
  --document-name AWS-StartPortForwardingSession \
  --parameters "portNumber=9000,localPortNumber=$LOCAL_PORT"
