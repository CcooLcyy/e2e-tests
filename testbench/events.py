"""工作进程的逐行事件通道。"""
from datetime import datetime
import json
import os
from pathlib import Path


def emit(event):
    target = os.environ.get("TESTBENCH_EVENTS")
    if target:
        event = {"time": datetime.now().astimezone().isoformat(timespec="seconds"), **event}
        with Path(target).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
