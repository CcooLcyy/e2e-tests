"""通过 pytest 真实收集业务用例，收集错误不会伪装成空列表。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from .model import CaseInfo


def discover_cases(root: Path):
    root = Path(root).resolve()
    source = str(Path(__file__).resolve().parent.parent)
    with tempfile.TemporaryDirectory(prefix="mskdsp-collect-") as temporary:
        output = Path(temporary) / "cases.json"
        env = dict(os.environ, PYTHONUTF8="1", PYTHONPATH=source + os.pathsep + os.environ.get("PYTHONPATH", ""),
                   TESTBENCH_COLLECT=str(output))
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", "-m", "pytest", "tests", "--rootdir", str(root), "--collect-only", "-q", "-p", "testbench.pytest_plugin",
             "-p", "no:cacheprovider"], cwd=root, env=env, capture_output=True, encoding="utf-8",
            errors="replace", timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode not in (0, 5):
            raise RuntimeError("用例发现失败：\n" + (result.stdout + result.stderr)[-6000:])
        if not output.exists():
            raise RuntimeError("用例收集未生成结果")
        return [CaseInfo(**item) for item in json.loads(output.read_text(encoding="utf-8"))]
