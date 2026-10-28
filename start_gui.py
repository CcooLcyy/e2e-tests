"""启动 React + Tauri 测试上位机，错误时给出中文提示。"""

import logging
from pathlib import Path
import sys
import traceback

from testbench.launcher import start_desktop


def main():
    root = Path(__file__).resolve().parent
    local = root / "local"
    local.mkdir(exist_ok=True)
    logging.basicConfig(filename=local / "gui.log", level=logging.INFO, encoding="utf-8", format="%(asctime)s %(levelname)s %(message)s")
    try:
        start_desktop(root, build_only="--build-only" in sys.argv[1:])
    except Exception as error:
        logging.exception("测试上位机发生错误")
        if sys.stderr is not None:
            traceback.print_exc()
            print(f"\n详细日志：{local / 'gui.log'}\n构建日志：{local / 'desktop-build.log'}", file=sys.stderr)
            return 1
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
