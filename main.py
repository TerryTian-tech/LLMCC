"""繁简转换工具 — GUI 入口。"""
import tkinter as tk
# certifi 供 requests 的 HTTPS 校验使用；显式导入以确保 PyInstaller
# 打包时将 CA 证书束带入，切勿当作无用 import 移除
import certifi
from gui.app import ConverterApp


def main() -> int:
    root = tk.Tk()
    app = ConverterApp(root)
    app.run()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
