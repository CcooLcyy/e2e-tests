"""启动 React + Tauri 测试上位机，错误时给出中文提示。"""

import logging
import os
from pathlib import Path
import subprocess
import sys
import traceback


def main():
    root = Path(__file__).resolve().parent
    local = root / "local"
    local.mkdir(exist_ok=True)
    logging.basicConfig(filename=local / "gui.log", level=logging.INFO, encoding="utf-8", format="%(asctime)s %(levelname)s %(message)s")
    try:
        candidates = [root / "src-tauri" / "target" / "release" / "mskdsp-testbench.exe",
                      root / "target" / "release" / "mskdsp-testbench.exe"]
        executable = next((path for path in candidates if path.is_file()), None)
        if executable is None:
            raise RuntimeError("尚未构建新版界面。请在仓库目录运行 npm install，然后运行 npm run desktop:build。")
        subprocess.Popen([str(executable)], cwd=root, env=dict(os.environ, TESTBENCH_ROOT=str(root)),
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as error:
        logging.exception("测试上位机发生错误")
        if sys.stderr is not None:
            traceback.print_exc()
        import tkinter as tk
        from tkinter import messagebox
        window = tk.Tk()
        window.withdraw()
        messagebox.showerror("测试上位机启动失败", f"{error}\n\n详细日志：{local / 'gui.log'}", parent=window)
        window.destroy()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
