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
        self.root.title("规范繁体字形转换器-AI繁简转换模块")
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

        self._tab_var = tk.StringVar(value="初始设置")
        self._tab_frames: list[ttk.Frame] = []

        for label in ["初始设置", "使用说明", "开始转换"]:
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

        self._build_page_settings(f1)
        self._build_page_about(f2)
        self._build_page_convert(f3)

        self._switch_tab("初始设置")

    def _switch_tab(self, label: str):
        idx = {"初始设置": 0, "使用说明": 1, "开始转换": 2}[label]
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
            frm, text="简体到规范繁体", variable=self.direction_var, value="s2t"
        ).pack(side="left", padx=(0, 20))
        ttk.Radiobutton(
            frm, text="繁体到简体", variable=self.direction_var, value="t2s"
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
            frm, text="记住 API Key",
            variable=self.save_key_var,
        ).grid(row=len(rows), column=1, sticky="w", pady=(pad, 0))

        frm = ttk.LabelFrame(parent, text="缓存", padding=pad)
        frm.pack(fill="x", pady=(0, pad))

        self.cache_info_var = tk.StringVar(value="加载中…")
        ttk.Label(frm, textvariable=self.cache_info_var).pack(side="left")
        ttk.Button(frm, text="清空缓存", command=self._clear_cache).pack(side="right")

        # 文本编码
        frm = ttk.LabelFrame(parent, text="文本编码", padding=pad)
        frm.pack(fill="x", pady=(pad, 0))

        ttk.Label(frm, text="强制编码：").pack(side="left")
        self.encoding_var = tk.StringVar(value=self.config.force_encoding or "自动检测")
        encodings = ["自动检测", "UTF-8", "GBK", "GB18030", "GBK", "Big5", "GB2312", "CP950", "UTF-16"]
        cb = ttk.Combobox(
            frm, textvariable=self.encoding_var, values=encodings,
            state="readonly", width=12,
        )
        cb.pack(side="left", padx=(pad, 0))
        ttk.Label(frm, text="（仅对 txt 生效）", foreground="gray").pack(
            side="left", padx=(pad, 0))

        # Word 文档选项
        frm = ttk.LabelFrame(parent, text="Word 文档", padding=pad)
        frm.pack(fill="x", pady=(pad, pad))

        self.preserve_fmt_var = tk.BooleanVar(value=self.config.preserve_format)
        ttk.Checkbutton(
            frm, text="保留格式（段落/字体/样式不丢失）",
            variable=self.preserve_fmt_var,
        ).pack(anchor="w")
        self.convert_fns_var = tk.BooleanVar(value=self.config.convert_footnotes)
        ttk.Checkbutton(
            frm, text="转换脚注与尾注",
            variable=self.convert_fns_var,
        ).pack(anchor="w")

    def _build_page_about(self, parent):
        info = ttk.Label(
            parent,
            text=(
                "规范繁体字形转换器-AI繁简转换模块\n\n"
                "本模块系“规范繁体字形转换器”的AI转换模块，因系独立构建，亦可脱离主程序单独使用。\n\n"
                "目前只支持繁—简、简—繁转换，对转换中出现的“一对多”情形，调用大模型 API 根据上下文语义判断。\n\n"
                "使用前请在初始设置里填写兼容OpenAI格式的API接口地址、模型名称、API Key， \n"
                "一般AI厂商的API平台所附接口文档会有说明。\n\n"
                "转换效果取决于模型能力，但一般远高于传统转换的正确率。\n\n"
                "虽然转换时只传输极少部分文本至AI厂商服务器，如果不希望文本被泄露，请不要使用本模块转换文件。\n"
                "AI转换过程耗时且需向AI厂商提取支付token费用，不建议转换长文本和大文件。\n\n"
                "模块版本：0.2.3 \n"
                "使用前请知悉本模块单独采用Anti-996-License 1.0许可证，主程序其余功能仍遵循Apache 2.0协议许可。\n\n"
                "本模块开源仓库地址：https://github.com/TerryTian-tech/LLMCC \n\n"
                "规范繁体字形转换器开源仓库地址：https://github.com/TerryTian-tech/OpenCC-Traditional- \n"
                "Chinese-characters-according-to-Chinese-government-standards \n"
            ),
            justify="left",
            anchor="w",
        )
        info.pack(fill="both", padx=10, pady=10)

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
        self.config.force_encoding = self.encoding_var.get()
        self.config.preserve_format = self.preserve_fmt_var.get()
        self.config.convert_footnotes = self.convert_fns_var.get()
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
        if self._worker_thread and self._worker_thread.is_alive():
            return
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
        if self._cancel_event.is_set():
            return
        ext = input_path.suffix.lower()
        log = lambda msg: self.root.after(0, self._log, msg)  # 线程安全
        enc = self.encoding_var.get()
        force_enc = None if enc == "自动检测" else enc

        if ext == '.txt':
            from text_converter import convert_txt_file
            result = convert_txt_file(
                str(input_path), str(output_dir), direction, self.converter,
                force_encoding=force_enc,
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
                    preserve_format=self.preserve_fmt_var.get(),
                    convert_footnotes=self.convert_fns_var.get(),
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
                preserve_format=self.preserve_fmt_var.get(),
                convert_footnotes=self.convert_fns_var.get(),
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
                force_encoding=force_enc,
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
