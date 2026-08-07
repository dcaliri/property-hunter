"""Integration test: scripts/cloud/check.sh (static IaC + shell validation).

Requires OpenTofu or Terraform on PATH; skipped otherwise. The live AWS
provision/destroy lifecycle cannot run in CI (needs an account) and is validated
manually per specs/002-cloud-provision-deploy/quickstart.md.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    shutil.which("terraform") is None and shutil.which("tofu") is None,
    reason="neither terraform nor tofu installed",
)


def _run(*args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True, **kw)


def test_check_script_passes():
    proc = _run("scripts/cloud/check.sh")
    assert proc.returncode == 0, proc.stderr
    assert "all static checks passed" in proc.stdout


def test_check_script_fails_on_bad_terraform():
    marker = "marker_bad.tf"
    (ROOT / "infra" / "aws" / marker).write_text("this is not valid HCL\n")
    try:
        proc = _run("scripts/cloud/check.sh")
    finally:
        (ROOT / "infra" / "aws" / marker).unlink(missing_ok=True)
    assert proc.returncode != 0


def test_all_cloud_scripts_have_valid_syntax():
    for path in sorted((ROOT / "scripts" / "cloud").rglob("*.sh")):
        proc = _run("bash", "-n", str(path))
        assert proc.returncode == 0, f"{path}: {proc.stderr}"


def test_help_flag_exits_zero_for_every_script():
    for name in ("bootstrap.sh", "up.sh", "down.sh", "status.sh", "dashboard.sh",
                 "secrets.sh", "deploy.sh"):
        proc = _run("scripts/cloud/" + name, "--help")
        assert proc.returncode == 0, name
        assert "Usage:" in proc.stdout
