#!/usr/bin/env bash
# check.sh — static validation for the cloud IaC + scripts.
#
# Runs, without touching AWS:
#   - terraform/tofu fmt -check -recursive on infra/aws
#   - terraform/tofu validate on the bootstrap and main configs
#   - sh -n / bash -n syntax checks on every scripts/cloud shell file
#
# Exit 0 on success, non-zero on the first failure.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# Prefer tofu, fall back to terraform (research §3).
TF=""
if command -v tofu >/dev/null 2>&1; then
  TF=tofu
elif command -v terraform >/dev/null 2>&1; then
  TF=terraform
else
  echo "check.sh: neither 'tofu' nor 'terraform' found — skipping IaC checks" >&2
  TF=""
fi

fail=0

check() {
  echo "==> $*"
  "$@"
}

if [ -n "$TF" ]; then
  check "$TF" fmt -check -recursive infra/aws || fail=1

  check "$TF" -chdir=infra/aws/bootstrap init -backend=false >/dev/null || fail=1
  check "$TF" -chdir=infra/aws/bootstrap validate || fail=1

  check "$TF" -chdir=infra/aws init -backend=false >/dev/null || fail=1
  check "$TF" -chdir=infra/aws validate || fail=1
fi

# Shell syntax checks on every script under scripts/cloud.
sh_files="$(find scripts/cloud -type f -name '*.sh' | sort)"
for f in $sh_files; do
  if head -1 "$f" | grep -q bash; then
    check bash -n "$f" || fail=1
  else
    check sh -n "$f" || fail=1
  fi
done

if [ "$fail" -eq 0 ]; then
  echo "check.sh: all static checks passed"
else
  echo "check.sh: FAILURES detected" >&2
  exit 1
fi
