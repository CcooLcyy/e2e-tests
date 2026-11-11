"""Prepare the signed Windows artifact and publish it from the protected CI job."""

import argparse
from base64 import b64decode
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from shutil import copyfile
import sys
import time
import tomllib
from urllib.request import Request, urlopen


PRODUCT = "mskdsp-testbench"
PLATFORM = "windows-x86_64-nsis"
BEIJING_TIME = timezone(timedelta(hours=8))
PUBLISH_TARGETS = {
    "ci": ("push", "TESTBENCH_CI_PUBLISHER_TOKEN"),
    "stable": ("workflow_dispatch", "TESTBENCH_PUBLISHER_TOKEN"),
}
ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "src-tauri/target/x86_64-pc-windows-msvc/release/bundle/nsis"
RELEASE = ROOT / "release"
TAURI_CONFIG = ROOT / "src-tauri/tauri.conf.json"


class ReleaseError(Exception):
    pass


class StaleRelease(ReleaseError):
    pass


def config_path():
    return Path(os.environ["RUNNER_TEMP"]) / "testbench-tauri-release.json"


def configure(channel):
    version = release_version(channel)
    endpoint = grpc_endpoint()
    key = os.environ.get("TESTBENCH_UPDATER_PUBLIC_KEY", "").strip()
    if not key:
        raise ReleaseError("Missing Testbench updater public key")
    config = json.loads((ROOT / "src-tauri/tauri.release.conf.json").read_text(encoding="utf-8"))
    config["version"] = version
    plugins = config.setdefault("plugins", {})
    # Tauri CLI needs this key to generate the NSIS updater signature.
    plugins["updater"] = {"pubkey": key}
    plugins["testbenchUpdate"] = {"endpoint": endpoint, "pubkey": key}
    if case_key := os.environ.get("CASE_PACK_PUBLIC_KEY", "").strip():
        minisign_key_id(case_key, 42)
        plugins["testbenchCasesUpdate"] = {"endpoint": endpoint, "pubkey": case_key}
    config_path().write_text(json.dumps(config), encoding="utf-8")


def package_version():
    version = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))["version"]
    if not isinstance(version, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){2}", version):
        raise ReleaseError("Stable version must use MAJOR.MINOR.PATCH")
    npm = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    npm_lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    cargo = tomllib.loads((ROOT / "src-tauri/Cargo.toml").read_text(encoding="utf-8"))
    cargo_lock = tomllib.loads((ROOT / "src-tauri/Cargo.lock").read_text(encoding="utf-8"))
    cargo_versions = [entry["version"] for entry in cargo_lock["package"] if entry["name"] == PRODUCT]
    versions = (npm["version"], npm_lock["version"], npm_lock["packages"][""]["version"], cargo["package"]["version"])
    if versions != (version,) * len(versions) or cargo_versions != [version]:
        raise ReleaseError("Synchronize the version in the npm, Cargo, and Tauri manifests and lockfiles")
    return version


def release_version(channel, recorded=None, *, allow_previous_attempt=False):
    version = package_version()
    if channel == "stable":
        if recorded is not None and recorded != version:
            raise ReleaseError("Stable build version differs from the source version")
        return version

    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    if not re.fullmatch(r"[1-9][0-9]*", run_attempt):
        raise ReleaseError("Missing or invalid GitHub run attempt")
    major, minor, patch = version.split(".")
    prefix = f"{major}.{minor}.{int(patch) + 1}-ci."
    if recorded is None:
        timestamp = datetime.now(BEIJING_TIME).strftime("%Y%m%d%H%M%S%f")[:-3]
        return f"{prefix}{timestamp}.{run_attempt}"

    # A publish-only rerun keeps the signed artifact from its original build attempt.
    pattern = re.escape(prefix) + r"([1-9][0-9]{16})\.([1-9][0-9]*)"
    match = re.fullmatch(pattern, recorded) if isinstance(recorded, str) else None
    if not match:
        raise ReleaseError("CI build version differs from this workflow attempt")
    timestamp, attempt = match.groups()
    if attempt != run_attempt and not (allow_previous_attempt and int(attempt) < int(run_attempt)):
        raise ReleaseError("CI build version differs from this workflow attempt")
    try:
        datetime.fromisoformat(f"{timestamp[:8]}T{timestamp[8:14]}.{timestamp[14:]}")
    except ValueError:
        raise ReleaseError("CI build version has an invalid Beijing timestamp") from None
    return recorded


def identity(archive, version, channel):
    signature = archive.with_suffix(".exe.sig").read_text(encoding="utf-8").strip()
    size = archive.stat().st_size
    if not signature or not size:
        raise ReleaseError("Installer or updater signature is empty")
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return dict(
        product=PRODUCT, platform=PLATFORM, channel=channel, version=version,
        file_name=archive.name, size=size, sha256=digest, signature=signature,
        source_repository=f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}",
        source_ref="main", source_sha=os.environ["GITHUB_SHA"],
    )


def release_archive(version):
    return RELEASE / f"{PRODUCT}_{version}_x64-setup.exe"


def minisign_key_id(box, payload_size):
    # Tauri wraps the minisign text in base64. The second line contains the binary
    # algorithm (2 bytes), key ID (8 bytes), and public key or signature bytes.
    try:
        payload = b64decode(b64decode(box, validate=True).splitlines()[1], validate=True)
    except (IndexError, ValueError):
        raise ReleaseError("Invalid Tauri updater key or signature encoding") from None
    if len(payload) != payload_size or payload[:2] not in (b"Ed", b"ED"):
        raise ReleaseError("Invalid Tauri updater key or signature payload")
    return payload[2:10]


def stage(channel):
    config = json.loads(config_path().read_text(encoding="utf-8"))
    if not isinstance(config["version"], str):
        raise ReleaseError("Missing Tauri build version")
    version = release_version(channel, config["version"])
    product_name = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))["productName"]
    installers = list(BUNDLE.glob("*.exe"))
    if len(installers) != 1 or installers[0].name != f"{product_name}_{version}_x64-setup.exe":
        raise ReleaseError("Expected exactly one version-matched x64 NSIS installer")
    installer = installers[0]
    archive = release_archive(version)
    RELEASE.mkdir(exist_ok=False)
    copyfile(installer, archive)
    copyfile(installer.with_suffix(".exe.sig"), archive.with_suffix(".exe.sig"))
    metadata = identity(archive, version, channel)
    updater = config["plugins"]["testbenchUpdate"]
    public_key = updater["pubkey"]
    if minisign_key_id(public_key, 42) != minisign_key_id(metadata["signature"], 74):
        raise ReleaseError("Updater signature uses a different key than the configured public key")
    lines = b64decode(metadata["signature"], validate=True).decode("utf-8").splitlines()
    comment = rf"trusted comment: timestamp:[0-9]+\tfile:{re.escape(installer.name)}\tversion:{re.escape(version)}"
    if len(lines) != 4 or not re.fullmatch(comment, lines[2]):
        raise ReleaseError("Updater signature must bind the original NSIS filename and app version")
    recorded = dict(metadata, endpoint=updater["endpoint"])
    if channel == "ci":
        recorded["run_id"] = os.environ["GITHUB_RUN_ID"]
    (RELEASE / "metadata.json").write_text(json.dumps(recorded, indent=2) + "\n", encoding="utf-8")


def grpc_endpoint():
    value = os.environ.get("PLATFORM_GRPC_ENDPOINT", "")
    match = re.fullmatch(r"http://(?:[A-Za-z0-9][A-Za-z0-9.-]*|\[[A-Fa-f0-9:]+\]):([1-9][0-9]{0,4})", value)
    if not match or int(match[1]) > 65535:
        raise ReleaseError("Platform plaintext gRPC endpoint must use http://host:port")
    return value


def require_current_main():
    api = os.environ["GITHUB_API_URL"]
    repo = os.environ["GITHUB_REPOSITORY"]
    request = Request(f"{api}/repos/{repo}/git/ref/heads/main", headers={
        "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
        "Accept": "application/vnd.github+json",
    })
    with urlopen(request, timeout=30) as response:
        if json.load(response)["object"]["sha"] != os.environ["GITHUB_SHA"]:
            raise StaleRelease("Publication requires the current main commit")


def transfer(archive, expected, endpoint, token):
    import grpc
    import software_pb2 as messages
    from software_pb2_grpc import SoftwareCatalogServiceStub, SoftwarePublishServiceStub

    auth = (("authorization", f"Bearer {token}"),)
    expected = messages.PackageMetadata(**expected)
    release_channel = expected.channel
    retryable = {grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED, grpc.StatusCode.CANCELLED}

    with grpc.insecure_channel(endpoint.removeprefix("http://"), options=(("grpc.enable_http_proxy", 0),)) as channel:
        publisher = SoftwarePublishServiceStub(channel)
        created = publisher.CreateUpload(messages.CreateUploadRequest(metadata=expected), metadata=auth, timeout=30).upload
        upload_id = created.id
        if not upload_id:
            raise ReleaseError("Platform did not return an upload ID")

        def chunks(offset):
            with archive.open("rb") as source:
                source.seek(offset)
                while block := source.read(1024 * 1024):
                    yield messages.UploadPackageRequest(upload_id=upload_id, offset=offset, data=block)
                    offset += len(block)

        for attempt in range(3):
            try:
                current = publisher.GetUpload(messages.GetUploadRequest(upload_id=upload_id), metadata=auth, timeout=30).upload
                if (current.id != upload_id or current.metadata != expected or current.received_bytes > expected.size
                        or current.state not in ("uploading", "completed")):
                    raise ReleaseError("Platform upload state differs from the artifact identity")
                if current.state == "uploading" and current.received_bytes < expected.size:
                    progress = publisher.UploadPackage(chunks(current.received_bytes), metadata=auth, timeout=480).upload
                    if progress.id != upload_id or progress.received_bytes != expected.size:
                        raise ReleaseError("Platform did not confirm all package bytes")
                if current.state == "completed" and (current.received_bytes != expected.size or not current.package_id):
                    raise ReleaseError("Completed upload has an invalid identity")
                # CompleteUpload is idempotent and returns the package even after an interrupted response.
                package = publisher.CompleteUpload(messages.CompleteUploadRequest(upload_id=upload_id), metadata=auth, timeout=90).package
                break
            except grpc.RpcError as error:
                if error.code() not in retryable or attempt == 2:
                    raise
                time.sleep(2 ** attempt)

        if not package.id or package.metadata != expected:
            raise ReleaseError("Stored package differs from the artifact identity")
        require_current_main()
        released = publisher.PublishRelease(
            messages.PublishReleaseRequest(package_id=package.id, channel=release_channel), metadata=auth, timeout=90,
        ).package
        if released.id != package.id or released.metadata != expected or release_channel not in released.channels:
            raise ReleaseError(f"Platform did not confirm publication to {release_channel}")
        print(f"Platform {release_channel} publication confirmed by RPC for {expected.product} {expected.version}, package {package.id}", flush=True)
        confirm_catalog(SoftwareCatalogServiceStub(channel), expected, package.id)


def confirm_catalog(catalog, expected, package_id):
    import software_pb2 as messages

    packages = catalog.ListPackages(messages.ListPackagesRequest(
        product=expected.product, platform=expected.platform, channel=expected.channel,
    ), timeout=30).packages
    if (len(packages) != 1 or packages[0].id != package_id or packages[0].metadata != expected
            or expected.channel not in packages[0].channels):
        raise ReleaseError("Catalog entry differs from the published package identity")

    stream = catalog.DownloadPackage(messages.DownloadPackageRequest(package_id=package_id), timeout=480)
    digest = hashlib.sha256()
    received = 0
    eof = False
    for chunk in stream:
        if (eof or chunk.offset != received or chunk.total_size != expected.size
                or chunk.sha256 != expected.sha256 or not chunk.data
                or received + len(chunk.data) > expected.size):
            raise ReleaseError("Package download has an invalid chunk identity")
        digest.update(chunk.data)
        received += len(chunk.data)
        eof = chunk.eof
    if not eof or received != expected.size or digest.hexdigest() != expected.sha256:
        raise ReleaseError("Package download differs from the published package bytes")


def publish(channel):
    event, token_variable = PUBLISH_TARGETS[channel]
    if os.environ.get("GITHUB_EVENT_NAME") != event or os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ReleaseError(f"Publishing to {channel} requires a {event} event on main")
    endpoint = grpc_endpoint()
    token = os.environ.get(token_variable, "")
    if not token or any(not 33 <= ord(char) <= 126 for char in token):
        raise ReleaseError(f"Missing or invalid {token_variable}")
    recorded = json.loads((RELEASE / "metadata.json").read_text(encoding="utf-8"))
    if channel == "ci" and recorded.pop("run_id", None) != os.environ["GITHUB_RUN_ID"]:
        raise ReleaseError("CI installer was not built by this workflow run")
    version = release_version(channel, recorded["version"], allow_previous_attempt=True)
    archive = release_archive(version)
    metadata = identity(archive, version, channel)
    if dict(metadata, endpoint=endpoint) != recorded:
        raise ReleaseError("Installer artifact or gRPC endpoint differs from the recorded build identity")
    notes = os.environ.get("TESTBENCH_RELEASE_NOTES", "") if channel == "stable" else ""
    if len(notes.encode("utf-8")) > 65536:
        raise ReleaseError("Release notes exceed 65536 UTF-8 bytes")
    metadata["notes"] = notes
    import grpc

    try:
        require_current_main()
        transfer(archive, metadata, endpoint, token)
    except StaleRelease:
        if channel != "ci":
            raise
        print("Skipped CI publication because this commit is no longer current on main")
        return
    except grpc.RpcError as error:
        raise ReleaseError(f"Platform gRPC returned {error.code().name}") from None
    print(f"Verified the public gRPC {channel} catalog entry and installer SHA256")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("configure", "stage", "publish"))
    parser.add_argument("channel", choices=PUBLISH_TARGETS)
    args = parser.parse_args()
    try:
        {"configure": configure, "stage": stage, "publish": publish}[args.action](args.channel)
    except ReleaseError as error:
        print(f"Release {args.action} failed. {error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"Release {args.action} failed ({type(error).__name__})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
