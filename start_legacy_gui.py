"""测试上位机入口；窗口模式出错时保留日志并给出中文提示。"""

import logging
from pathlib import Path
import sys
import traceback


def main():
    root = Path(__file__).resolve().parent
    local = root / "local"
    local.mkdir(exist_ok=True)
    logging.basicConfig(filename=local / "gui.log", level=logging.INFO, encoding="utf-8", format="%(asctime)s %(levelname)s %(message)s")
    try:
        from testbench.gui import main as launch
        launch(root)
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
