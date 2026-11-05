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


def invoke(script, *args, input_text=None, environment=None):
    powershell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return subprocess.run(
        [str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *args],
        capture_output=True, text=True, timeout=60, check=False, input=input_text,
        env={**{key: value for key, value in os.environ.items() if key.lower() != "psmodulepath"},
             **(environment or {})},
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
    result = invoke(package / "Install.ps1", "-InstallDirectory", str(target), "-NoShortcuts", "-NonInteractive")
    assert result.returncode != 0
    assert "checksum mismatch" in result.stderr
    assert not target.exists()


def test_installer_does_not_overwrite_existing_directory(package, tmp_path):
    target = tmp_path / "app"
    target.mkdir()
    sentinel = target / "keep.txt"
    sentinel.write_text("keep")
    result = invoke(package / "Install.ps1", "-InstallDirectory", str(target), "-NoShortcuts", "-NonInteractive")
    assert result.returncode != 0
    assert "already exists" in result.stderr
    assert sentinel.read_text() == "keep"


def test_uninstaller_refuses_uninstalled_package(package):
    result = invoke(package / "Uninstall.ps1", "-ConfirmRemoval")
    assert result.returncode != 0
    assert "installed copy" in result.stderr
    assert (package / "payload").is_dir()


def stub_runtime_setup(package):
    bootstrap = package / "payload" / "scripts" / "bootstrap.ps1"
    bootstrap.write_text(
        "param([switch]$RuntimeOnly)\n"
        "$root=Split-Path $PSScriptRoot -Parent\n"
        "New-Item -ItemType Directory -Force \"$root\\.tools\\runtime\" | Out-Null\n"
        "Set-Content \"$root\\.tools\\runtime\\owned.txt\" 'runtime'\n"
    )
    manifest_path = package / "payload-sha256.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    for entry in manifest:
        if entry["path"] == r"scripts\bootstrap.ps1":
            entry["sha256"] = hashlib.sha256(bootstrap.read_bytes()).hexdigest().upper()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def test_install_prompts_persist_data_and_uninstall_preserves_user_files(package, tmp_path):
    stub_runtime_setup(package)
    target = tmp_path / "chosen app"
    data = tmp_path / "map data"
    data.mkdir()
    (data / "existing-map.pmtiles").write_bytes(b"map")
    result = invoke(package / "Install.ps1", "-NoShortcuts",
                    input_text=f"{target}\n{data}\n")
    assert result.returncode == 0, result.stderr
    settings = json.loads((target / "fieldwork-settings.json").read_text(encoding="utf-8-sig"))
    assert settings["data_directory"] == str(data)
    (target / "backend" / "user-notes.txt").write_text("keep nested file")
    (target / ".tools" / "user.txt").write_text("keep runtime addition")
    (target / "backend" / "user-empty-directory").mkdir()
    (target / "README.md").write_text("user-modified")
    result = invoke(target / "Uninstall.ps1", "-ConfirmRemoval")
    assert result.returncode == 0, result.stderr
    assert (target / "backend" / "user-notes.txt").read_text() == "keep nested file"
    assert (target / ".tools" / "user.txt").read_text() == "keep runtime addition"
    assert (target / "backend" / "user-empty-directory").is_dir()
    assert (target / "README.md").read_text() == "user-modified"
    assert (data / "existing-map.pmtiles").read_bytes() == b"map"
    assert not (target / ".tools" / "runtime" / "owned.txt").exists()
    assert not (target / "installation.json").exists()


@pytest.mark.parametrize("data_location", ["same", "inside", "parent"])
def test_install_rejects_overlapping_data_directory(package, tmp_path, data_location):
    target = tmp_path / "app"
    data = {"same": target, "inside": target / "maps", "parent": tmp_path}[data_location]
    result = invoke(package / "Install.ps1", "-InstallDirectory", str(target),
                    "-DataDirectory", str(data), "-NonInteractive", "-NoShortcuts")
    assert result.returncode != 0
    assert "must be separate" in result.stderr
    assert not target.exists()


def test_install_accepts_empty_directory_and_explicit_parameters(package, tmp_path):
    stub_runtime_setup(package)
    target = tmp_path / "empty app"
    target.mkdir()
    result = invoke(package / "Install.ps1", "-InstallDirectory", str(target),
                    "-DataDirectory", str(tmp_path / "maps \u00e4\u5730"), "-NonInteractive", "-NoShortcuts")
    assert result.returncode == 0, result.stderr
    assert (target / "installation.json").exists()


@pytest.mark.parametrize("target", [
    r"C:\ ", r"relative\app", r"\\server\app", r"C:\bad*\app", r"C:\CON\app", r"C:\app[1]",
])
def test_install_rejects_unsafe_directory_paths(package, tmp_path, target):
    result = invoke(package / "Install.ps1", "-InstallDirectory", target,
                    "-DataDirectory", str(tmp_path / "maps"), "-NonInteractive", "-NoShortcuts")
    assert result.returncode != 0
    assert not (tmp_path / "maps").exists()


def test_uninstall_rejects_outside_ownership_paths(package, tmp_path):
    stub_runtime_setup(package)
    target = tmp_path / "app"
    result = invoke(package / "Install.ps1", "-InstallDirectory", str(target),
                    "-DataDirectory", str(tmp_path / "maps"), "-NonInteractive", "-NoShortcuts")
    assert result.returncode == 0, result.stderr
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    record_path = target / "installation.json"
    record = json.loads(record_path.read_text(encoding="utf-8-sig"))
    record["files"].append({"path": r"..\outside.txt", "sha256": hashlib.sha256(outside.read_bytes()).hexdigest()})
    record_path.write_text(json.dumps(record))
    result = invoke(target / "Uninstall.ps1", "-ConfirmRemoval")
    assert result.returncode != 0
    assert "ownership path" in result.stderr
    assert outside.read_text() == "keep"
    assert (target / "scripts" / "start.ps1").exists()


def test_install_enter_accepts_default_directories(package, tmp_path):
    stub_runtime_setup(package)
    local = tmp_path / "local"
    result = invoke(package / "Install.ps1", "-NoShortcuts", input_text="\n\n",
                    environment={"LOCALAPPDATA": str(local)})
    assert result.returncode == 0, result.stderr
    settings = json.loads((local / "Fieldwork" / "fieldwork-settings.json").read_text(encoding="utf-8-sig"))
    assert settings["data_directory"] == str(local / "MapCreator")


def test_uninstall_refuses_junction_without_touching_target(package, tmp_path):
    import _winapi

    stub_runtime_setup(package)
    target = tmp_path / "app"
    result = invoke(package / "Install.ps1", "-InstallDirectory", str(target),
                    "-DataDirectory", str(tmp_path / "maps"), "-NonInteractive", "-NoShortcuts")
    assert result.returncode == 0, result.stderr
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    junction = target / "backend" / "junction"
    _winapi.CreateJunction(str(outside), str(junction))
    try:
        result = invoke(target / "Uninstall.ps1", "-ConfirmRemoval")
        assert result.returncode != 0
        assert "junctions/symbolic links" in result.stderr
        assert (outside / "keep.txt").read_text() == "keep"
        assert (target / "scripts" / "start.ps1").exists()
    finally:
        os.rmdir(junction)


def test_interrupted_uninstall_retains_a_working_retry(package, tmp_path):
    stub_runtime_setup(package)
    target = tmp_path / "app"
    result = invoke(package / "Install.ps1", "-InstallDirectory", str(target),
                    "-DataDirectory", str(tmp_path / "maps"), "-NonInteractive", "-NoShortcuts")
    assert result.returncode == 0, result.stderr
    with (target / "backend" / "app" / "__init__.py").open("rb"):
        result = invoke(target / "Uninstall.ps1", "-ConfirmRemoval")
    assert result.returncode != 0
    assert (target / "Uninstall.ps1").exists()
    assert (target / "Paths.ps1").exists()
    assert (target / "installation.json").exists()
    result = invoke(target / "Uninstall.ps1", "-ConfirmRemoval")
    assert result.returncode == 0, result.stderr
