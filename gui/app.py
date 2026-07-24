"""tkinter 图形界面 — 左右布局 + 选项卡。"""
import ctypes
import logging
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import warnings

# Windows 高 DPI 感知（解决字体发虚）
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PerMonitorV2
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import sv_ttk

from ts_converter.ai_client import AIClient
from ts_converter.cache import TranslationCache
from ts_converter.config import load_config, save_config
from ts_converter.converter import Converter
from ts_converter.mapping import load_mappings

# 抑制第三方库的弃用警告
warnings.filterwarnings("ignore", message=".*pkg_resources is deprecated.*")

DEFAULT_CONFIG_PATH = Path.home() / ".ts_converter_config.json"
DEFAULT_CACHE_PATH = Path.home() / ".ts_converter_cache.json"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class _GuiLogHandler(logging.Handler):
    """将 Python logging 消息转发到 GUI 日志框（线程安全）。"""

    def __init__(self, log_callback, root):
        super().__init__()
        self._log_callback = log_callback
        self._root = root

    def emit(self, record: logging.LogRecord):
        msg = self.format(record)
        self._root.after(0, self._log_callback, msg)


class ConverterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("AI繁简转换工具-V0.2.2")
        self.root.geometry("960x680")

        # 窗口居中
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - 960) // 2
        y = (sh - 680) // 2
        self.root.geometry(f"+{x}+{y}")

        # 图标
        icon_path = Path(__file__).resolve().parent.parent / "logo.ico"
        if icon_path.exists():
            self.root.iconbitmap(default=str(icon_path))

        self._cancel_event = threading.Event()
        self._worker_thread = None

        self.config = load_config(DEFAULT_CONFIG_PATH)
        self.cache = TranslationCache(DEFAULT_CACHE_PATH)
        try:
            self.mappings = load_mappings(DATA_DIR)
        except FileNotFoundError as e:
            messagebox.showerror("启动失败", str(e))
            self.root.destroy()
            return
        self.ai_client = AIClient(self.config)

        # sv_ttk 主题
        sv_ttk.set_theme(self.config.theme)

        self._build_ui()

        handler = _GuiLogHandler(self._log, self.root)
        handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
        logging.getLogger("ts_converter").addHandler(handler)
        logging.getLogger("ts_converter").setLevel(logging.INFO)

        self._update_cache_info()

    # ── UI 构建 ──────────────────────────────────────────

    def _build_ui(self):
        pad = 10

        # ── 主体：左标签栏 + 右内容区 ──
        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True, padx=pad, pady=pad)

        # 左侧标签栏
        tab_bar = ttk.Frame(body, width=110)
        tab_bar.pack(side="left", fill="y")
        tab_bar.pack_propagate(False)

        inner_bar = ttk.Frame(tab_bar)
        inner_bar.pack(fill="y", padx=4, pady=4)

        self._tab_var = tk.StringVar(value="文件转换")
        self._tab_frames: list[ttk.Frame] = []

        for label in ["文件转换", "设置", "关于"]:
            btn = ttk.Radiobutton(
                inner_bar, text=label, variable=self._tab_var, value=label,
                command=lambda l=label: self._switch_tab(l),
            )
            btn.pack(fill="x", pady=2, ipady=4)

        # 右侧内容区
        self._content_area = ttk.Frame(body)
        self._content_area.pack(side="left", fill="both", expand=True, padx=(pad, 0))

        f1 = ttk.Frame(self._content_area)
        f2 = ttk.Frame(self._content_area)
        f3 = ttk.Frame(self._content_area)
        self._tab_frames = [f1, f2, f3]

        self._build_page_convert(f1)
        self._build_page_settings(f2)
        self._build_page_about(f3)

        self._switch_tab("文件转换")

    def _switch_tab(self, label: str):
        idx = {"文件转换": 0, "设置": 1, "关于": 2}[label]
        for f in self._tab_frames:
            f.pack_forget()
        self._tab_frames[idx].pack(fill="both", expand=True)

    def _build_page_convert(self, parent):
        pad = 5

        # 按钮 + 进度条
        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(0, pad))

        self.convert_btn = ttk.Button(
            bar, text="开始转换", command=self._start_convert
        )
        self.convert_btn.pack(side="left", padx=(0, 5))
        self.cancel_btn = ttk.Button(
            bar, text="取消转换", command=self._cancel_convert, state="disabled"
        )
        self.cancel_btn.pack(side="left", padx=(0, 10))
        self.progress = ttk.Progressbar(bar, mode="indeterminate")
        self.progress.pack(side="left", fill="x", expand=True)

        frm = ttk.LabelFrame(parent, text="输入", padding=pad)
        frm.pack(fill="x", pady=(0, pad))

        self.input_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.input_var).pack(
            side="left", fill="x", expand=True, padx=(0, pad)
        )
        ttk.Button(frm, text="选择文件", command=self._browse_input_file).pack(
            side="left", padx=2
        )
        ttk.Button(frm, text="选择文件夹", command=self._browse_input_dir).pack(
            side="left"
        )

        frm = ttk.LabelFrame(parent, text="输出文件夹", padding=pad)
        frm.pack(fill="x", pady=(0, pad))

        self.output_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.output_var).pack(
            side="left", fill="x", expand=True, padx=(0, pad)
        )
        ttk.Button(frm, text="选择文件夹", command=self._browse_output_dir).pack(
            side="left"
        )

        frm = ttk.LabelFrame(parent, text="转换方向", padding=pad)
        frm.pack(fill="x", pady=(0, pad))

        self.direction_var = tk.StringVar(value="s2t")
        ttk.Radiobutton(
            frm, text="简 → 繁", variable=self.direction_var, value="s2t"
        ).pack(side="left", padx=(0, 20))
        ttk.Radiobutton(
            frm, text="繁 → 简", variable=self.direction_var, value="t2s"
        ).pack(side="left")

        ttk.Label(
            parent,
            text="输出文件名由转换器自动生成（如 convert_xxx.txt）",
            foreground="gray",
        ).pack(anchor="w", pady=(0, pad))

        # 日志
        log_frame = ttk.LabelFrame(parent, text="日志", padding=5)
        log_frame.pack(fill="both", expand=True)

        self.log_box = tk.Text(
            log_frame, state="disabled", wrap="word",
            font=("Consolas", 9),
        )
        log_scroll = ttk.Scrollbar(log_frame, command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=log_scroll.set)
        self.log_box.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

    def _build_page_settings(self, parent):
        pad = 5

        # 主题
        frm = ttk.LabelFrame(parent, text="主题", padding=pad)
        frm.pack(fill="x", pady=(0, pad))

        self.theme_var = tk.StringVar(value=self.config.theme)
        ttk.Radiobutton(
            frm, text="浅色", variable=self.theme_var, value="light",
            command=lambda: [sv_ttk.set_theme("light"), self._save_config()],
        ).pack(side="left", padx=(0, 20))
        ttk.Radiobutton(
            frm, text="暗色", variable=self.theme_var, value="dark",
            command=lambda: [sv_ttk.set_theme("dark"), self._save_config()],
        ).pack(side="left")

        # 转换模式
        frm = ttk.LabelFrame(parent, text="转换模式", padding=pad)
        frm.pack(fill="x", pady=(0, pad))

        self.quality_var = tk.BooleanVar(value=self.config.quality_mode)
        ttk.Radiobutton(
            frm, text="质量优先（不完整时重试）",
            variable=self.quality_var, value=True,
            command=self._save_config,
        ).pack(anchor="w")
        ttk.Radiobutton(
            frm, text="速度优先（不完整时跳过）",
            variable=self.quality_var, value=False,
            command=self._save_config,
        ).pack(anchor="w")

        frm = ttk.LabelFrame(parent, text="API 配置", padding=pad)
        frm.pack(fill="x", pady=(0, pad))

        self.base_url_var = tk.StringVar(value=self.config.api_base_url)
        self.model_var = tk.StringVar(value=self.config.api_model)
        self.key_var = tk.StringVar(
            value=self.config.api_key if self.config.save_api_key else ""
        )
        self.ctx_window_var = tk.StringVar(value=str(self.config.context_window))
        self.save_key_var = tk.BooleanVar(value=self.config.save_api_key)

        rows = [
            ("Base URL：", self.base_url_var, None),
            ("模型：", self.model_var, None),
            ("API Key：", self.key_var, "*"),
            ("上下文窗口：", self.ctx_window_var, None),
        ]
        for i, (label, var, show) in enumerate(rows):
            ttk.Label(frm, text=label).grid(row=i, column=0, sticky="w", pady=2)
            entry = ttk.Entry(frm, textvariable=var)
            if show:
                entry.configure(show=show)
            entry.grid(row=i, column=1, sticky="we", padx=(pad, 0), pady=2)
        frm.columnconfigure(1, weight=1)

        ttk.Checkbutton(
            frm, text="记住 API Key（明文保存）",
            variable=self.save_key_var,
        ).grid(row=len(rows), column=1, sticky="w", pady=(pad, 0))

        frm = ttk.LabelFrame(parent, text="缓存", padding=pad)
        frm.pack(fill="x", pady=(0, pad))

        self.cache_info_var = tk.StringVar(value="加载中…")
        ttk.Label(frm, textvariable=self.cache_info_var).pack(side="left")
        ttk.Button(frm, text="清空缓存", command=self._clear_cache).pack(side="right")

    def _build_page_about(self, parent):
        info = ttk.Label(
            parent,
            text=(
                "AI繁简转换工具-V0.2.2\n\n"
                "支持简—繁、繁—简的中文文本转换。\n"
                "对一对多歧义字调用大模型 API 根据上下文语义判断。\n\n"
                "支持文件格式：TXT / SRT / ASS / LRC / DOC / DOCX / EPUB\n\n"
                "开源仓库主页：https://github.com/TerryTian-tech/LLMCC\n"
            ),
            justify="center",
            anchor="center",
        )
        info.pack(fill="both", expand=True, padx=10, pady=10)

    # ── 日志 ──────────────────────────────────────────────

    def _log(self, message: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", message + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    # ── 浏览 ──────────────────────────────────────────────

    def _browse_input_file(self):
        path = filedialog.askopenfilename(
            filetypes=[
                ("所有支持的文件", "*.txt;*.srt;*.ass;*.ssa;*.lrc;*.doc;*.docx;*.epub"),
                ("文本文件", "*.txt"),
                ("字幕文件", "*.srt;*.ass;*.ssa;*.lrc"),
                ("Word 文档", "*.doc;*.docx"),
                ("EPUB 电子书", "*.epub"),
                ("所有文件", "*.*"),
            ]
        )
        if path:
            self.input_var.set(path)

    def _browse_input_dir(self):
        path = filedialog.askdirectory()
        if path:
            self.input_var.set(path)

    def _browse_output_dir(self):
        path = filedialog.askdirectory()
        if path:
            self.output_var.set(path)

    # ── 缓存管理 ──────────────────────────────────────────

    def _count_cache_entries(self) -> int:
        return self.cache.entry_count()

    def _update_cache_info(self):
        self.cache_info_var.set(f"本地缓存：{self._count_cache_entries()} 条")

    def _clear_cache(self):
        if not messagebox.askyesno("确认", "确定要清空所有本地缓存吗？"):
            return
        self.cache.clear()
        self.cache.save()
        self._update_cache_info()
        self._log("已清空本地缓存")

    # ── 配置 ──────────────────────────────────────────────

    def _save_config(self):
        self.config.theme = self.theme_var.get()
        self.config.quality_mode = self.quality_var.get()
        save_config(self.config, DEFAULT_CONFIG_PATH)

    def _update_config_from_ui(self):
        self.config.api_base_url = self.base_url_var.get().strip()
        self.config.api_model = self.model_var.get().strip()
        self.config.api_key = self.key_var.get().strip()
        self.config.save_api_key = self.save_key_var.get()
        try:
            self.config.context_window = int(self.ctx_window_var.get())
        except ValueError:
            self.config.context_window = 10
        save_config(self.config, DEFAULT_CONFIG_PATH)

    # ── 转换 ──────────────────────────────────────────────

    def _start_convert(self):
        self._update_config_from_ui()
        self.ai_client = AIClient(self.config)
        self.converter = Converter(
            self.mappings, self.ai_client, self.cache, self.config,
            quality_mode=self.quality_var.get(),
            cancel_event=self._cancel_event,
        )

        input_path = Path(self.input_var.get())
        output_dir = Path(self.output_var.get()) if self.output_var.get() else None
        direction = self.direction_var.get()

        if not input_path.exists():
            messagebox.showerror("错误", "输入路径不存在")
            return
        if not output_dir:
            messagebox.showerror("错误", "请选择输出文件夹")
            return
        if not self.config.api_key.strip():
            messagebox.showerror("错误", "请先填写 API Key")
            return

        output_dir.mkdir(parents=True, exist_ok=True)

        self._cancel_event.clear()
        self.progress.start()
        self.convert_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")

        self._worker_thread = threading.Thread(
            target=self._convert_worker,
            args=(input_path, output_dir, direction),
        )
        self._worker_thread.daemon = True
        self._worker_thread.start()

    def _cancel_convert(self):
        self._cancel_event.set()
        self._log("正在取消…")

    def _convert_worker(self, input_path, output_dir, direction):
        try:
            if input_path.is_file():
                self._convert_single_file(input_path, output_dir, direction)
            elif input_path.is_dir():
                self._convert_folder(input_path, output_dir, direction)
            else:
                self.root.after(
                    0, lambda: messagebox.showerror("错误", "输入路径无效")
                )
        except Exception as e:
            err = str(e)
            self.root.after(0, lambda msg=err: messagebox.showerror("错误", msg))
        finally:
            self.cache.save()
            self.root.after(0, self._update_cache_info)
            self.root.after(0, self.progress.stop)
            self.root.after(0, lambda: self.convert_btn.configure(state="normal"))
            self.root.after(0, lambda: self.cancel_btn.configure(state="disabled"))
            self._worker_thread = None

    def _convert_single_file(self, input_path, output_dir, direction):
        ext = input_path.suffix.lower()
        log = self._log

        if ext == '.txt':
            from text_converter import convert_txt_file
            result = convert_txt_file(
                str(input_path), str(output_dir), direction, self.converter,
                log_callback=log, is_cancelled_callback=self._cancel_event.is_set,
            )
        elif ext in ('.srt',):
            from text_converter import convert_srt_file
            result = convert_srt_file(
                str(input_path), str(output_dir), direction, self.converter,
                log_callback=log, is_cancelled_callback=self._cancel_event.is_set,
            )
        elif ext in ('.ass', '.ssa'):
            from text_converter import convert_ass_file
            result = convert_ass_file(
                str(input_path), str(output_dir), direction, self.converter,
                log_callback=log, is_cancelled_callback=self._cancel_event.is_set,
            )
        elif ext == '.lrc':
            from text_converter import convert_lrc_file
            result = convert_lrc_file(
                str(input_path), str(output_dir), direction, self.converter,
                log_callback=log, is_cancelled_callback=self._cancel_event.is_set,
            )
        elif ext == '.doc':
            from doc_converter import convert_doc_to_docx
            docx_path = convert_doc_to_docx(
                str(input_path), str(output_dir),
                log_callback=log, is_cancelled_callback=self._cancel_event.is_set,
            )
            if docx_path:
                from doc_converter import convert_docx_file
                result = convert_docx_file(
                    docx_path, str(output_dir), direction, self.converter,
                    log_callback=log, is_cancelled_callback=self._cancel_event.is_set,
                )
                try:
                    os.remove(docx_path)  # 清理 DOC→DOCX 中间文件
                except OSError:
                    pass
            else:
                result = False
        elif ext == '.docx':
            from doc_converter import convert_docx_file
            result = convert_docx_file(
                str(input_path), str(output_dir), direction, self.converter,
                log_callback=log, is_cancelled_callback=self._cancel_event.is_set,
            )
        elif ext == '.epub':
            from epub_converter import convert_epub_file
            result = convert_epub_file(
                str(input_path), str(output_dir), direction, self.converter,
                log_callback=log, is_cancelled_callback=self._cancel_event.is_set,
            )
        else:
            from text_converter import convert_txt_file
            result = convert_txt_file(
                str(input_path), str(output_dir), direction, self.converter,
                log_callback=log, is_cancelled_callback=self._cancel_event.is_set,
            )
        self.root.after(0, lambda r=result: self._log(f"完成：{r}" if r else "转换失败"))

    def _convert_folder(self, input_path, output_dir, direction):
        supported = ('*.txt', '*.srt', '*.ass', '*.ssa', '*.lrc',
                     '*.doc', '*.docx', '*.epub')
        files = []
        for pattern in supported:
            files.extend(input_path.glob(pattern))
        files = sorted(files, key=lambda p: p.name)

        if not files:
            self.root.after(0, lambda: self._log("输入文件夹中没有支持的文件"))
            return

        self.root.after(0, lambda: self._log(f"共发现 {len(files)} 个文件"))
        for f in files:
            if self._cancel_event.is_set():
                self.root.after(0, lambda: self._log("已取消转换"))
                break
            self._convert_single_file(f, output_dir, direction)

    # ── 主循环 ────────────────────────────────────────────

    def run(self):
        self.root.mainloop()
