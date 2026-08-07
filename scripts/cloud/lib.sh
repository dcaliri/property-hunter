#!/usr/bin/env bash
# Shared helpers for the scripts/cloud/* operator CLI. Sourced, not executed.
# Provides tool detection, preflights, the retained-resource safety gate, and
# the status-output JSON emitter (contracts/status-output-v1.schema.json).

set -uo pipefail

BUDGET_USD_MONTH="${BUDGET_USD_MONTH:-5}"
export BUDGET_USD_MONTH
FREE_TRIAL_END="2026-12-31"          # t4g.small free-trial window (research §1)
EBS_GP3_PRICE_PER_GB=0.08
PUBLIC_IPV4_USD_MONTH=3.65           # ~$0.005/h while a public IPv4 is in use
T4G_MICRO_USD_MONTH=6.13             # on-demand 24/7 (research §10)
T4G_SMALL_USD_MONTH=12.26            # on-demand 24/7 (research §10)
BOOTSTRAP_ENV="infra/aws/.bootstrap.env"
VOLUME_TAG_NAME="property-hunter-data"

log()  { printf '%s\n' "$*" >&2; }
die()  { log "ERROR: $*"; exit 2; }
fail() { log "ERROR: $*"; exit 1; }

now_iso() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

find_tf() {
  local tf
  if command -v tofu >/dev/null 2>&1; then tf=tofu
  elif command -v terraform >/dev/null 2>&1; then tf=terraform
  else die "neither 'tofu' nor 'terraform' found on PATH"
  fi
  printf '%s' "$tf"
}

require() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command '$1'${2:+ ($2)}"
}

region_from_az() {
  printf '%s' "$1" | sed -E 's/[a-z]$//'
}

# resolve_ref_tag <ref> -> docker-safe image tag
resolve_ref_tag() {
  local tag
  tag="$(printf '%s' "$1" | tr -c 'a-zA-Z0-9_.-' '-')"
  if [ -z "$tag" ]; then tag=latest; fi
  printf '%s' "$tag"
}

# resolve_ref_sha <ref> -> short commit sha for the ref (must exist locally)
resolve_ref_sha() {
  require git "git is required to resolve --ref"
  git rev-parse --short --verify "$1" 2>/dev/null \
    || die "git ref '$1' not found locally (fetch it first)"
}

# ECR registry host + repo (bootstrap-owned, retained)
ecr_registry() {
  require aws
  local account region
  account="$(aws sts get-caller-identity --query Account --output text)"
  [ -z "$account" ] && die "could not resolve AWS account id"
  region="${1:-$(aws configure get region || echo us-east-1)}"
  printf '%s.dkr.ecr.%s.amazonaws.com' "$account" "$region"
}

# Retained-resource safety gate: refuse a plan that would create or destroy the
# bootstrap-owned resources (S3 bucket / EBS volume / ECR repo), which live in
# the bootstrap state and must never be touched by the main stack.
# gate_plan <plan-file>
gate_plan() {
  local tf plan="$1"
  tf="$(find_tf)"
  if "$tf" show "$plan" 2>/dev/null | grep -E '^ *[+-] aws_(s3_bucket|ebs_volume|ecr_repository)\.' >/dev/null; then
    log "Refusing to proceed: plan touches a retained resource (S3 bucket / EBS volume / ECR repo)."
    log "These live in the bootstrap state; investigate before continuing."
    return 1
  fi
  return 0
}

# ssm_run <instance> <region> <command-text> -> command id
ssm_run() {
  local iid="$1" region="$2" cmds="$3"
  local cmds_json
  cmds_json="$(python3 -c 'import json,sys; print(json.dumps([sys.stdin.read().rstrip(chr(10))]))' <<< "$cmds")" \
    || die "python3 is required"
  aws ssm send-command --instance-ids "$iid" --document-name AWS-RunShellScript \
    --region "$region" --parameters "{\"commands\":$cmds_json}" \
    --query Command.CommandId --output text
}

# ssm_wait <instance> <region> <command-id> [timeout-seconds]
ssm_wait() {
  local iid="$1" region="$2" cmd_id="$3"
  local timeout="${4:-600}" status="" waited=0
  while [ "$waited" -lt "$timeout" ]; do
    status="$(aws ssm get-command-invocation --command-id "$cmd_id" --instance-id "$iid" \
      --region "$region" --query Status --output text 2>/dev/null || echo Pending)"
    case "$status" in
      Success) return 0 ;;
      Failed|Cancelled|TimedOut)
        log "SSM command ${status}:"
        aws ssm get-command-invocation --command-id "$cmd_id" --instance-id "$iid" \
          --region "$region" --query StandardErrorContent --output text >&2 || true
        return 1 ;;
    esac
    sleep 5
    waited=$((waited + 5))
  done
  log "timed out waiting for SSM command ($cmd_id)"
  return 1
}

# ssm_wait_online <instance> <region> [timeout-seconds]
ssm_wait_online() {
  local iid="$1" region="$2"
  local timeout="${3:-600}" ping="" waited=0
  while [ "$waited" -lt "$timeout" ]; do
    ping="$(aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$iid" \
      --region "$region" --query "InstanceInformationList[0].PingStatus" --output text 2>/dev/null || echo NotFound)"
    if [ "$ping" = "Online" ]; then return 0; fi
    sleep 5
    waited=$((waited + 5))
  done
  log "timed out waiting for instance $iid to register with SSM"
  return 1
}

# resolve the running environment's instance id by tag (used by dashboard/status).
running_instance_id() {
  require aws
  aws ec2 describe-instances \
    --filters "Name=tag:Name,Values=property-hunter" "Name=instance-state-name,Values=running" \
    --query "Reservations[].Instances[0].InstanceId" --output text 2>/dev/null || true
}

# set_cost_env: populate the ENV_COST_* variables for emit_status_json.
# Reads ENV_INSTANCE_TYPE, ENV_VOLUME_SIZE, ENV_PUBLIC_IP.
set_cost_env() {
  local compute=0 pubip=0 ebs today instance_type="${ENV_INSTANCE_TYPE:-}"
  today="$(date -u +%F)"
  if [ -n "$instance_type" ]; then
    case "$instance_type" in
      t4g.small)
        if [ "$today" \< "$FREE_TRIAL_END" ]; then compute=0; else compute="$T4G_SMALL_USD_MONTH"; fi ;;
      *)
        compute="$T4G_MICRO_USD_MONTH" ;;
    esac
    if [ "${ENV_PUBLIC_IP:-0}" = "1" ]; then pubip="$PUBLIC_IPV4_USD_MONTH"; fi
  fi
  ebs="$(python3 -c "print(round(float('${ENV_VOLUME_SIZE:-20}') * $EBS_GP3_PRICE_PER_GB, 2))")"
  ENV_COST_ESTIMATED="$(python3 -c "print(round($compute + $pubip + $ebs, 2))")"
  ENV_COST_BUDGET="${ENV_COST_BUDGET:-$BUDGET_USD_MONTH}"
  if python3 -c "exit(0 if float('$ENV_COST_ESTIMATED') > float('$ENV_COST_BUDGET') else 1)"; then
    ENV_COST_OVER="true"
  else
    ENV_COST_OVER="false"
  fi
  ENV_COST_ASSUMPTIONS="EBS gp3 ${ENV_VOLUME_SIZE:-20} GiB at \$0.08/GiB-mo (billed while retained)
Compute ${instance_type:-n/a}: free under the t4g free-trial window until $FREE_TRIAL_END, else on-demand 24/7
Public IPv4 ~\$3.65/mo while the instance runs (outbound egress for SSM/ECR/app); may be covered by the account free allowance
S3 state + SSM parameters ≈ \$0.00/mo
24/7 running assumed; part-time weekly cycles cost less"
}

# Discover the retained volume id (+ tags) by its Name tag.
volume_by_tag() {
  require aws
  aws ec2 describe-volumes --filters "Name=tag:Name,Values=$VOLUME_TAG_NAME" \
    --query "Volumes[0]" --output json 2>/dev/null || printf 'null'
}

# Read infra/aws/.bootstrap.env into the calling shell (best-effort).
load_bootstrap_env() {
  if [ -f "$BOOTSTRAP_ENV" ]; then
    # shellcheck disable=SC1090
    . "$BOOTSTRAP_ENV"
  fi
}

# emit_status_json: print the status-output-v1 JSON document.
# Reads the ENV_* variables set by the caller (see scripts/cloud/status.sh).
emit_status_json() {
  require python3 "python3 is required for --json output"
  export ENV_STATE ENV_INSTANCE_ID ENV_INSTANCE_TYPE ENV_AZ ENV_APP_REF
  export ENV_RETAINED_BUCKET ENV_RETAINED_VOLUME ENV_RETAINED_SIZE
  export ENV_COST_ESTIMATED ENV_COST_BUDGET ENV_COST_OVER ENV_COST_ASSUMPTIONS
  export ENV_GENERATED_AT ENV_PUBLIC_IP
  python3 - <<'PY'
import json, os
def s(k, d=""):
    return os.environ.get(k, d)
state = s("ENV_STATE", "")
environment = None
if state:
    environment = {
        "instance_id": s("ENV_INSTANCE_ID"),
        "instance_type": s("ENV_INSTANCE_TYPE"),
        "availability_zone": s("ENV_AZ"),
        "state": state,
        "app_ref": s("ENV_APP_REF"),
        "public_access": "ssm-tunnel",
    }
cost = {
    "estimated_usd_month": round(float(s("ENV_COST_ESTIMATED", "0") or 0), 2),
    "budget_usd_month": float(s("ENV_COST_BUDGET") or os.environ.get("BUDGET_USD_MONTH", "5")),
    "over_budget": s("ENV_COST_OVER", "false") == "true",
}
assumptions = [a for a in s("ENV_COST_ASSUMPTIONS", "").split("\n") if a]
if assumptions:
    cost["assumptions"] = assumptions
doc = {
    "tool": "property-hunter-cloud",
    "version": "v1",
    "generated_at": s("ENV_GENERATED_AT"),
    "environment": environment,
    "retained": {
        "state_bucket": s("ENV_RETAINED_BUCKET"),
        "data_volume_id": s("ENV_RETAINED_VOLUME"),
        "data_volume_size_gb": int(float(s("ENV_RETAINED_SIZE", "0") or 0)),
    },
    "cost": cost,
}
print(json.dumps(doc, indent=2))
PY
}
