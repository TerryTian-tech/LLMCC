"""繁简转换工具 — GUI 入口。"""
import tkinter as tk

from gui.app import ConverterApp


def main() -> int:
    root = tk.Tk()
    app = ConverterApp(root)
    app.run()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
