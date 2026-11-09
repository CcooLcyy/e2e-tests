"""Prepare the signed Windows artifact and publish it from the protected CI job."""

import argparse
from base64 import b64decode
from contextlib import closing
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
from shutil import copyfile
import sys
import time
import tomllib
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


PRODUCT = "mskdsp-testbench"
PLATFORM = "windows-x86_64-nsis"
CHANNEL = "stable"
ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "src-tauri/target/x86_64-pc-windows-msvc/release/bundle/nsis"
RELEASE = ROOT / "release"


class ReleaseError(Exception):
    pass


def https_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.query or parsed.fragment or "\\" in value or any(c.isspace() for c in value)):
        raise ReleaseError("Expected an HTTPS URL without credentials, query, or fragment")
    return parsed


def manifest_url():
    value = os.environ.get("TESTBENCH_UPDATER_MANIFEST_URL", "")
    parsed = https_url(value)
    if not parsed.path.endswith(f"/{PRODUCT}/{CHANNEL}/latest.json"):
        raise ReleaseError("Updater URL must point to the Testbench Stable manifest")
    return parsed


def config_path():
    return Path(os.environ["RUNNER_TEMP"]) / "testbench-tauri-release.json"


def configure():
    package_version()
    manifest = manifest_url()
    key = os.environ.get("TESTBENCH_UPDATER_PUBLIC_KEY", "").strip()
    if not key:
        raise ReleaseError("Missing Testbench updater public key")
    config = json.loads((ROOT / "src-tauri/tauri.release.conf.json").read_text(encoding="utf-8"))
    config.setdefault("plugins", {})["updater"] = {
        "pubkey": key,
        "endpoints": [manifest.geturl()],
    }
    config_path().write_text(json.dumps(config), encoding="utf-8")


def package_version():
    version = json.loads((ROOT / "src-tauri/tauri.conf.json").read_text(encoding="utf-8"))["version"]
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


def identity(archive, version):
    signature = archive.with_suffix(".exe.sig").read_text(encoding="utf-8").strip()
    size = archive.stat().st_size
    if not signature or not size:
        raise ReleaseError("Installer or updater signature is empty")
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return dict(
        product=PRODUCT, platform=PLATFORM, channel=CHANNEL, version=version,
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


def stage():
    version = package_version()
    installers = list(BUNDLE.glob("*.exe"))
    if len(installers) != 1 or not installers[0].name.endswith(f"_{version}_x64-setup.exe"):
        raise ReleaseError("Expected exactly one version-matched x64 NSIS installer")
    installer = installers[0]
    archive = release_archive(version)
    RELEASE.mkdir(exist_ok=False)
    copyfile(installer, archive)
    copyfile(installer.with_suffix(".exe.sig"), archive.with_suffix(".exe.sig"))
    metadata = identity(archive, version)
    config = json.loads(config_path().read_text(encoding="utf-8"))
    public_key = config["plugins"]["updater"]["pubkey"]
    if minisign_key_id(public_key, 42) != minisign_key_id(metadata["signature"], 74):
        raise ReleaseError("Updater signature uses a different key than the configured public key")
    (RELEASE / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


def grpc_endpoint():
    value = os.environ.get("PLATFORM_GRPC_ENDPOINT", "")
    endpoint = value.removeprefix("http://")
    match = re.fullmatch(r"(?:[A-Za-z0-9][A-Za-z0-9.-]*|\[[A-Fa-f0-9:]+\]):([1-9][0-9]{0,4})", endpoint)
    if not match or int(match[1]) > 65535:
        raise ReleaseError("Platform plaintext gRPC endpoint must be host:port or http://host:port")
    return endpoint


def require_current_main():
    api = os.environ["GITHUB_API_URL"]
    repo = os.environ["GITHUB_REPOSITORY"]
    request = Request(f"{api}/repos/{repo}/git/ref/heads/main", headers={
        "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
        "Accept": "application/vnd.github+json",
    })
    with urlopen(request, timeout=30) as response:
        if json.load(response)["object"]["sha"] != os.environ["GITHUB_SHA"]:
            raise ReleaseError("Stable dispatch is stale. Run the workflow from current main")


def transfer(archive, expected, endpoint, token):
    import grpc
    import software_pb2 as messages
    from software_pb2_grpc import SoftwarePublishServiceStub

    auth = (("authorization", f"Bearer {token}"),)
    expected = messages.PackageMetadata(**expected)
    retryable = {grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED, grpc.StatusCode.CANCELLED}

    with grpc.insecure_channel(endpoint, options=(("grpc.enable_http_proxy", 0),)) as channel:
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
                        raise ReleaseError("Platform did not confirm all installer bytes")
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
            messages.PublishReleaseRequest(package_id=package.id, channel=CHANNEL), metadata=auth, timeout=90,
        ).package
        if released.id != package.id or released.metadata != expected or CHANNEL not in released.channels:
            raise ReleaseError("Platform did not confirm publication to Stable")
        return package.id


def confirm_manifest(manifest, metadata, package_id):
    with closing(http.client.HTTPSConnection(manifest.hostname, manifest.port, timeout=30)) as connection:
        connection.request("GET", manifest.path, headers={"Cache-Control": "no-cache"})
        response = connection.getresponse()
        if response.status != 200:
            raise ReleaseError(f"Stable publication RPC succeeded, but manifest returned HTTP {response.status}")
        result = json.loads(response.read(256 * 1024))
    asset = result.get("platforms", {}).get(PLATFORM, {})
    download = https_url(asset.get("url", ""))
    if (result.get("version") != metadata["version"] or result.get("notes") != metadata["notes"]
            or asset.get("signature") != metadata["signature"]
            or not download.path.endswith(f"/software/packages/{package_id}/{metadata['file_name']}")):
        raise ReleaseError("Stable publication RPC succeeded, but manifest differs from the installer identity")


def publish():
    import grpc

    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch" or os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ReleaseError("Stable publishing requires a manual dispatch on main")
    endpoint = grpc_endpoint()
    manifest = manifest_url()
    token = os.environ.get("TESTBENCH_PUBLISHER_TOKEN", "")
    if not token or any(not 33 <= ord(char) <= 126 for char in token):
        raise ReleaseError("Missing or invalid Testbench upload token")
    version = package_version()
    archive = release_archive(version)
    metadata = identity(archive, version)
    if metadata != json.loads((RELEASE / "metadata.json").read_text(encoding="utf-8")):
        raise ReleaseError("Installer artifact differs from the recorded build identity")
    notes = os.environ.get("TESTBENCH_RELEASE_NOTES", "")
    if len(notes.encode("utf-8")) > 65536:
        raise ReleaseError("Release notes exceed 65536 UTF-8 bytes")
    metadata["notes"] = notes
    require_current_main()
    try:
        package_id = transfer(archive, metadata, endpoint, token)
    except grpc.RpcError as error:
        raise ReleaseError(f"Platform gRPC returned {error.code().name}") from None
    print(f"Platform Stable publication confirmed by RPC for {PRODUCT} {version}, package {package_id}", flush=True)
    confirm_manifest(manifest, metadata, package_id)
    print("Verified the public HTTPS updater manifest")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("configure", "stage", "publish"))
    action = parser.parse_args().action
    try:
        {"configure": configure, "stage": stage, "publish": publish}[action]()
    except ReleaseError as error:
        print(f"Release {action} failed. {error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"Release {action} failed ({type(error).__name__})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
