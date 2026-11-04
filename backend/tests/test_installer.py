import hashlib
import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VERSION = json.loads((ROOT / "web" / "package.json").read_text())["version"]
ARCHIVE = ROOT / "release" / f"Fieldwork-{VERSION}-windows-x64.zip"
pytestmark = pytest.mark.skipif(
    os.name != "nt" or not ARCHIVE.exists(), reason="Build Windows installer ZIP first"
)


@pytest.fixture
def package(tmp_path):
    with zipfile.ZipFile(ARCHIVE) as archive:
        archive.extractall(tmp_path / "extracted")
    return tmp_path / "extracted" / ARCHIVE.stem


def invoke(script, *args):
    powershell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return subprocess.run(
        [str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *args],
        capture_output=True, text=True, timeout=30, check=False,
        env={key: value for key, value in os.environ.items() if key.lower() != "psmodulepath"},
    )


def test_installer_contains_complete_checked_payload(package):
    manifest = json.loads((package / "payload-sha256.json").read_text(encoding="utf-8-sig"))
    payload = package / "payload"
    assert len(manifest) == len([p for p in payload.rglob("*") if p.is_file()])
    for entry in manifest:
        assert hashlib.sha256((payload / entry["path"]).read_bytes()).hexdigest().upper() == entry["sha256"]
    for filename in ("Install.cmd", "Install.ps1", "Uninstall.ps1", "INSTALL.txt"):
        assert (package / filename).is_file()
    for filename in ("web/dist/index.html", "tiles/RegionProfile.java", "backend/app/main.py",
                     "tools/win-64.lock", "tools/python.lock", "THIRD_PARTY_LICENSES.txt"):
        assert (payload / filename).is_file()
    assert not (payload / ".tools").exists()
    assert not (payload / "web" / "node_modules").exists()


def test_installer_rejects_modified_payload_without_installing(package, tmp_path):
    (package / "payload" / "README.md").write_text("corrupted")
    target = tmp_path / "app"
    result = invoke(package / "Install.ps1", "-InstallDirectory", str(target), "-NoShortcuts")
    assert result.returncode != 0
    assert "checksum mismatch" in result.stderr
    assert not target.exists()


def test_installer_does_not_overwrite_existing_directory(package, tmp_path):
    target = tmp_path / "app"
    target.mkdir()
    sentinel = target / "keep.txt"
    sentinel.write_text("keep")
    result = invoke(package / "Install.ps1", "-InstallDirectory", str(target), "-NoShortcuts")
    assert result.returncode != 0
    assert "already exists" in result.stderr
    assert sentinel.read_text() == "keep"


def test_uninstaller_refuses_uninstalled_package(package):
    result = invoke(package / "Uninstall.ps1", "-ConfirmRemoval")
    assert result.returncode != 0
    assert "installed copy" in result.stderr
    assert (package / "payload").is_dir()
