"""The deploy path: images Jelastic accepts, signed, deployed by commit tag.

Every test here pins a lesson somebody already paid for, most of them on the
sibling project wiemeinsch.ch's go-live on the same platform (2026-09-27):
Jelastic refuses a custom container on an unsupported distribution, it has no
redeploy webhook, and its API reports failure as HTTP 200. The first deploy of
this repository added one more: a gha build cache needs buildx.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

# Bases Jelastic accepts as a custom container (Virtuozzo's list; amd64 only).
# Debian 13 is NOT on it - the bare `python:3.13-slim` tag moved there.
SUPPORTED_BASE = re.compile(
    r"^(debian:bookworm|python:[\d.]+-(slim-)?bookworm|alpine:3\.|"
    r"almalinux:9|ubuntu:(18|20|22|24)\.04)"
)


def _workflow(name):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _final_bases(dockerfile):
    """The FROM of every stage, minus the build-only stages copied from."""
    froms = re.findall(r"^FROM\s+(\S+)(?:\s+AS\s+(\S+))?", dockerfile.read_text(), re.M)
    copied_from = set(re.findall(r"--from=(\S+)", dockerfile.read_text()))
    return [image for image, alias in froms if alias not in copied_from]


@pytest.mark.parametrize("dockerfile", ["Dockerfile", "tunnel/Dockerfile"])
def test_every_runtime_base_is_one_jelastic_accepts(dockerfile):
    bases = _final_bases(ROOT / dockerfile)
    assert bases, dockerfile
    for base in bases:
        assert SUPPORTED_BASE.match(base), f"{dockerfile}: {base} is not a supported base"


def test_the_tunnel_image_is_pinned_and_not_root():
    text = (ROOT / "tunnel" / "Dockerfile").read_text()
    for image in re.findall(r"^FROM\s+(\S+)", text, re.M):
        assert "@sha256:" in image, f"{image} is not pinned by digest"
    user = re.search(r"^USER\s+(\S+)", text, re.M)
    assert user and not user.group(1).startswith(("0", "root"))


def test_the_deploy_builds_only_on_main_after_the_checks_and_with_buildx():
    deploy = _workflow("deploy-prod.yml")
    jobs = deploy["jobs"]
    assert jobs["test"]["uses"] == "./.github/workflows/checks.yml"
    build = jobs["build-push"]
    assert build["needs"] == "test"
    assert build["if"] == "github.ref == 'refs/heads/main'"
    uses = [step.get("uses", "") for step in build["steps"]]
    buildx = next(i for i, u in enumerate(uses) if u.startswith("docker/setup-buildx-action"))
    push = next(i for i, u in enumerate(uses) if u.startswith("docker/build-push-action"))
    assert buildx < push, "the gha cache needs buildx set up before the build"
    assert any("cosign sign" in step.get("run", "") for step in build["steps"])
    assert jobs["deploy"]["uses"] == "./.github/workflows/redeploy.yml"
    assert jobs["deploy"]["with"]["tag"] == "${{ github.sha }}"


def test_the_redeploy_verifies_uses_the_api_and_reads_the_result_field():
    redeploy = _workflow("redeploy.yml")
    job = redeploy["jobs"]["redeploy"]
    assert job["environment"] == "production"
    assert job["concurrency"]["group"] == "production-redeploy"
    script = "\n".join(step.get("run", "") for step in job["steps"])
    assert "^[0-9a-f]{40}$" in script, "only a full commit SHA is deployable"
    assert "cosign verify" in script
    assert "redeploycontainersbygroup" in script
    # HTTP 200 with result != 0 is a failure on this API.
    assert ".result" in script
    # The token travels in the body via stdin, never in a URL.
    assert "session@-" in script
    assert "JELASTIC_WEBHOOK" not in (WORKFLOWS / "deploy-prod.yml").read_text()


def test_a_rollback_goes_through_the_same_verification():
    rollback = _workflow("rollback.yml")
    assert rollback["jobs"]["redeploy"]["uses"] == "./.github/workflows/redeploy.yml"


BACKUP = ROOT / "scripts" / "backup"


@pytest.mark.parametrize("script", ["backup.sh", "restore.sh", "rotate.sh"])
def test_the_backup_scripts_are_strict_and_keep_secrets_off_command_lines(script):
    text = (BACKUP / script).read_text(encoding="utf-8")
    assert "set -euo pipefail" in text
    # The password is read from the environment by libpq; a command line is
    # visible to every process on the node.
    assert "--password" not in text and "PGPASSWORD=" not in text


def test_the_backups_keep_the_privacy_policys_thirty_days():
    backup = (BACKUP / "backup.sh").read_text(encoding="utf-8")
    rotate = (BACKUP / "rotate.sh").read_text(encoding="utf-8")
    assert "CLASS=monthly" not in backup
    assert "rotate_class monthly" not in rotate
    keep_weekly = int(re.search(r"BACKUP_KEEP_WEEKLY:-(\d+)", rotate).group(1))
    assert keep_weekly * 7 <= 30


BASH = shutil.which("bash")


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.parametrize("script", ["backup.sh", "restore.sh", "rotate.sh"])
def test_the_backup_scripts_parse(script):
    # The resolved path, not "bash": on Windows a bare "bash" can start WSL's
    # System32 launcher instead of the Git Bash that `which` found.
    result = subprocess.run(  # noqa: S603 - fixed arguments, no user input
        [BASH, "-n", str(BACKUP / script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
