"""工作进程的逐行事件通道。"""
from datetime import datetime
import json
import os
from pathlib import Path
import time


def timestamp():
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


class ExecutionTiming:
    def __init__(self):
        self.started_at = timestamp()
        self._started = time.monotonic()

    def finish(self):
        return {"started_at": self.started_at, "finished_at": timestamp(),
                "duration": round(time.monotonic() - self._started, 3)}


def emit(event):
    target = os.environ.get("TESTBENCH_EVENTS")
    if target:
        event = {"time": timestamp(), **event}
        with Path(target).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
