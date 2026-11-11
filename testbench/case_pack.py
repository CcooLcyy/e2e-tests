"""选择用例目录并启用桌面更新器已验签的用例包。"""
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import unicodedata
import uuid
import zipfile

from .workspace import _is_plain


RUNNER_API = 1
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_EXTRACTED_BYTES = 256 * 1024 * 1024
MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_ENTRIES = 4096
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?")
SOURCE_SHA = re.compile(r"[0-9a-f]{40}")
ARCHIVE_SHA = re.compile(r"[0-9a-f]{64}")
PACK_DIRECTORY = re.compile(r"[0-9a-f]{64}-[0-9a-f]{8}")
WINDOWS_DEVICE = re.compile(r"CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[1-9¹²³]", re.I)


@dataclass(frozen=True)
class CasePack:
    root: Path
    source: str
    version: str = ""
    source_sha: str = ""
    sha256: str = ""

    def status(self):
        return {"source": self.source, "version": self.version, "source_sha": self.source_sha,
                "sha256": self.sha256}


def _matches(pattern, value):
    return isinstance(value, str) and len(value) <= 128 and pattern.fullmatch(value) is not None


def _read_object(path):
    if not _is_plain(path, path.is_file) or path.stat().st_size > 16 * 1024:
        raise ValueError(f"用例包记录无效：{path.name}")
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError(f"用例包记录必须为 JSON 对象：{path.name}")
    return result


def _read_manifest(root):
    manifest = _read_object(root / "case-pack.json")
    if type(manifest.get("runner_api")) is not int or manifest["runner_api"] != RUNNER_API:
        raise ValueError("用例包与当前测试上位机不兼容")
    version, source_sha = manifest.get("version"), manifest.get("source_sha")
    if not _matches(VERSION, version) or not _matches(SOURCE_SHA, source_sha):
        raise ValueError("用例包清单缺少有效的版本或源码提交号")
    tests = root / "tests"
    if not _is_plain(tests, tests.is_dir) or not any(tests.rglob("test_*.py")):
        raise ValueError("用例包缺少 tests/ 下的 test_*.py")
    for relative in ("pytest.ini", "tests/conftest.py", "tests/case-order.json"):
        path = root / relative
        if not _is_plain(path, path.is_file):
            raise ValueError(f"用例包缺少 {relative}")
    return version, source_sha


def _archive_entries(archive):
    entries = archive.infolist()
    if len(entries) > MAX_ENTRIES or sum(entry.file_size for entry in entries) > MAX_EXTRACTED_BYTES:
        raise ValueError("用例包解压内容超过限制")
    paths, written = {}, set()
    for entry in entries:
        name = entry.orig_filename
        directory = entry.is_dir()
        parts = tuple(name.removesuffix("/").split("/"))
        if (len(name) > 240 or len(parts) > 16 or entry.filename != name
                or any(not part or part.endswith((".", " ")) or part in (".", "..")
                       or any(ord(char) < 32 or char in '\\:<>"|?*' for char in part)
                       or WINDOWS_DEVICE.fullmatch(part.split(".", 1)[0]) for part in parts)):
            raise ValueError("用例包包含无效路径")
        if len(parts) == 1:
            allowed = parts[0] in (("tests", "case_support", "docs") if directory else ("pytest.ini", "case-pack.json"))
        else:
            allowed = (parts[0] in ("tests", "case_support")
                       or (parts[:2] == ("docs", "examples") and (directory or len(parts) > 2)))
        if not allowed:
            raise ValueError(f"用例包包含不受支持的路径：{name}")
        mode = stat.S_IFMT(entry.external_attr >> 16)
        if (mode not in (0, stat.S_IFDIR if directory else stat.S_IFREG)
                or entry.external_attr & stat.FILE_ATTRIBUTE_REPARSE_POINT or entry.flag_bits & 1):
            raise ValueError("用例包包含链接、特殊文件或加密条目")
        if entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise ValueError("用例包包含不受支持的压缩方式")
        if entry.file_size > MAX_MEMBER_BYTES or (directory and entry.file_size):
            raise ValueError("用例包条目大小超过限制")
        key = tuple(unicodedata.normalize("NFC", part).casefold() for part in parts)
        for depth in range(1, len(parts) + 1):
            prefix = key[:depth]
            target = (parts[:depth], directory or depth < len(parts))
            if paths.setdefault(prefix, target) != target:
                raise ValueError("用例包包含路径冲突")
        if key in written:
            raise ValueError("用例包包含重复路径")
        written.add(key)
        yield entry, parts


def _extract_archive(source, digest, destination):
    with source.open("rb") as stream:
        if os.fstat(stream.fileno()).st_size > MAX_ARCHIVE_BYTES:
            raise ValueError("用例包压缩文件超过 64 MiB")
        if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
            raise ValueError("用例包压缩文件的 SHA256 校验失败")
        stream.seek(0)
        try:
            with zipfile.ZipFile(stream) as archive:
                for entry, parts in list(_archive_entries(archive)):
                    target = destination.joinpath(*parts)
                    if entry.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(entry) as original, target.open("xb") as output:
                            shutil.copyfileobj(original, output)
                            if output.tell() != entry.file_size:
                                raise ValueError("用例包条目大小与清单不符")
        except (zipfile.BadZipFile, NotImplementedError) as error:
            raise ValueError(f"用例包 ZIP 格式无效：{error}") from error


def _checkout_sha(root):
    if not (root / ".git").exists():
        return ""
    try:
        result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                                text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        sha = result.stdout.strip()
        return sha if result.returncode == 0 and _matches(SOURCE_SHA, sha) else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


class CasePackStore:
    def __init__(self, root, data_root):
        self.root = root
        self.data_root = data_root
        self.area = data_root / ".case-packs"
        self.pointer = self.area / "active.json"
        self._download_loaded = False
        self._selected = None
        self.warning = ""

    def _bundled(self):
        suite = self.root / "case-suite"
        if not (suite / "tests").is_dir():
            return CasePack(self.root, "legacy")
        if self.data_root != self.root or (suite / "case-pack.json").exists():
            version, sha = _read_manifest(suite)
            return CasePack(suite, "bundled", version, sha)
        version_file = suite / "VERSION"
        version = version_file.read_text(encoding="utf-8").strip() if version_file.is_file() else ""
        return CasePack(suite, "checkout", version, _checkout_sha(suite))

    def _active(self):
        if not (self.pointer.exists() or self.pointer.is_symlink()):
            return None
        if not _is_plain(self.area, self.area.is_dir):
            raise ValueError("用例包缓存目录不能是链接")
        pointer = _read_object(self.pointer)
        name, digest = pointer.get("directory"), pointer.get("sha256")
        if not _matches(PACK_DIRECTORY, name) or not _matches(ARCHIVE_SHA, digest) or not name.startswith(digest + "-"):
            raise ValueError("当前用例包记录无效")
        pack = self.area / name
        if not _is_plain(pack, pack.is_dir):
            raise ValueError("当前用例包目录不可用")
        version, sha = _read_manifest(pack)
        if version != pointer.get("version") or sha != pointer.get("source_sha"):
            raise ValueError("当前用例包与启用记录不一致")
        return CasePack(pack, "downloaded", version, sha, digest)

    def current(self):
        if not self._download_loaded:
            try:
                self._selected = self._active()
            except (OSError, ValueError) as error:
                self.use_bundled(error)
            self._download_loaded = True
        return self._selected or self._bundled()

    def use_bundled(self, error):
        self._selected = None
        self.warning = "本地下载的用例包不可用，已切换到随附用例。"
        logging.getLogger(__name__).warning("本地下载的用例包不可用，改用随附用例：%s", error)

    def activate(self, payload, discover):
        location, version = payload.get("path"), payload.get("version")
        source_sha, digest = payload.get("source_sha"), payload.get("sha256")
        if (not isinstance(location, str) or not _matches(VERSION, version)
                or not _matches(SOURCE_SHA, source_sha) or not _matches(ARCHIVE_SHA, digest)):
            raise ValueError("缺少用例包文件或有效的签名元数据")
        source = Path(location)
        downloads = self.data_root / ".case-downloads"
        if (not source.is_absolute() or not _is_plain(downloads, downloads.is_dir)
                or not _is_plain(source, source.is_file)
                or not source.resolve(strict=True).is_relative_to(downloads)):
            raise ValueError("用例包必须来自桌面下载目录")
        self.area.mkdir(exist_ok=True)
        if not _is_plain(self.area, self.area.is_dir):
            raise ValueError("用例包缓存目录不能是链接")
        with tempfile.TemporaryDirectory(prefix=".pending-", dir=self.area) as temporary:
            staging = Path(temporary)
            extracted = staging / "suite"
            extracted.mkdir()
            _extract_archive(source, digest, extracted)
            if _read_manifest(extracted) != (version, source_sha):
                raise ValueError("用例包清单与已验证的版本信息不一致")
            target = self.area / (digest + "-" + uuid.uuid4().hex[:8])
            selected = CasePack(target, "downloaded", version, source_sha, digest)
            pending = staging / self.pointer.name
            pending.write_text(json.dumps({**selected.status(), "directory": target.name}), encoding="utf-8")
            extracted.rename(target)
            try:
                cases = discover(target)
                if not cases:
                    raise ValueError("用例包未收集到可执行用例")
                if any(not case.id.startswith("tests/") for case in cases):
                    raise ValueError("用例包收集结果必须来自 tests 目录")
                pending.replace(self.pointer)
            except Exception:
                shutil.rmtree(target)
                raise
            self._selected = selected
            self._download_loaded = True
            self.warning = ""
        logging.getLogger(__name__).info("已启用用例包 %s，源码提交 %s，共 %s 项用例", version, source_sha, len(cases))
        return selected, cases
