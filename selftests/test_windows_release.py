from base64 import b64encode
from datetime import datetime, timezone
import json

import pytest

from scripts import windows_release


CI_TIMESTAMP = "20261110003045123"
CI_VERSION = f"0.1.10-ci.{CI_TIMESTAMP}.2"


def test_plaintext_platform_endpoint(monkeypatch):
    endpoint = "http://60.208.15.90:50000"
    monkeypatch.setenv("PLATFORM_GRPC_ENDPOINT", endpoint)
    assert windows_release.grpc_endpoint() == endpoint


@pytest.mark.parametrize("endpoint", [
    "",
    "60.208.15.90:50000",
    "https://60.208.15.90:50000",
    "http://60.208.15.90:50000/latest.json",
    "http://60.208.15.90:65536",
])
def test_rejects_invalid_platform_endpoint(monkeypatch, endpoint):
    monkeypatch.setenv("PLATFORM_GRPC_ENDPOINT", endpoint)
    with pytest.raises(windows_release.ReleaseError, match="http://host:port"):
        windows_release.grpc_endpoint()


@pytest.fixture
def ci_run(monkeypatch):
    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 9, 16, 30, 45, 123456, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(windows_release, "package_version", lambda: "0.1.9")
    monkeypatch.setattr(windows_release, "datetime", FixedClock)
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")


def test_versions_use_next_patch_for_ci_and_manifest_version_for_stable(ci_run):
    assert windows_release.release_version("ci") == CI_VERSION
    assert windows_release.release_version("stable") == "0.1.9"


def test_publish_only_rerun_accepts_original_ci_build_attempt(ci_run):
    original = "0.1.10-ci.20261109235959123.1"
    assert windows_release.release_version("ci", original, allow_previous_attempt=True) == original
    with pytest.raises(windows_release.ReleaseError, match="workflow attempt"):
        windows_release.release_version("ci", original)


@pytest.mark.parametrize("version", [
    "0.1.9",
    f"0.1.9-ci.{CI_TIMESTAMP}.2",
    "0.1.10-ci.12.2",
    "0.1.10-ci.20261132003045123.2",
    f"0.1.10-ci.{CI_TIMESTAMP}.0",
    f"0.1.10-ci.{CI_TIMESTAMP}.01",
    f"0.1.10-ci.{CI_TIMESTAMP}.3",
])
def test_ci_publish_rejects_other_build_versions(ci_run, version):
    with pytest.raises(windows_release.ReleaseError, match="build version"):
        windows_release.release_version("ci", version, allow_previous_attempt=True)


def test_stable_publish_rejects_ci_artifact_version(ci_run):
    with pytest.raises(windows_release.ReleaseError, match="build version"):
        windows_release.release_version("stable", CI_VERSION)


@pytest.mark.parametrize(("name", "signed_version", "error"), [
    (f"Testbench_{CI_VERSION}_x64-setup.exe", CI_VERSION, None),
    (f"Other_{CI_VERSION}_x64-setup.exe", CI_VERSION, "version-matched"),
    (f"Testbench_{CI_VERSION}_x64-setup.exe", "0.1.9", "signature must bind"),
])
def test_ci_stage_checks_original_filename_and_signed_version(ci_run, tmp_path, monkeypatch, name, signed_version, error):
    def minisign_box(payload, comment=b"unused"):
        return b64encode(b"untrusted comment\n" + b64encode(payload) + b"\n" + comment + b"\nunused").decode()

    endpoint = "http://platform.test:50000"
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    release = tmp_path / "release"
    tauri_config = tmp_path / "tauri.conf.json"
    tauri_config.write_text('{"productName": "Testbench"}', encoding="utf-8")
    monkeypatch.setattr(windows_release, "BUNDLE", bundle)
    monkeypatch.setattr(windows_release, "RELEASE", release)
    monkeypatch.setattr(windows_release, "TAURI_CONFIG", tauri_config)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/testbench")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    windows_release.config_path().write_text(json.dumps({
        "version": CI_VERSION,
        "plugins": {"testbenchUpdate": {"endpoint": endpoint, "pubkey": minisign_box(b"Ed" + b"a" * 40)}},
    }), encoding="utf-8")
    installer = bundle / name
    installer.write_bytes(b"installer bytes")
    comment = f"trusted comment: timestamp:1\tfile:{name}\tversion:{signed_version}".encode()
    installer.with_suffix(".exe.sig").write_text(minisign_box(b"ED" + b"a" * 72, comment), encoding="utf-8")

    if error:
        with pytest.raises(windows_release.ReleaseError, match=error):
            windows_release.stage("ci")
        return

    windows_release.stage("ci")
    metadata = json.loads((release / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["file_name"] == f"mskdsp-testbench_{CI_VERSION}_x64-setup.exe"
    assert metadata["version"] == CI_VERSION
    assert metadata["channel"] == "ci"
    assert metadata["endpoint"] == endpoint
    assert metadata["run_id"] == "123"
