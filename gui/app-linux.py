# Linux 兼容版本：与 gui/app.py 逻辑一致，但移除了仅限 Windows 的
# DOC（Word/WPS COM）转换入口，文件对话框与文件夹批量转换不再列出
# .doc；DOCX / TXT / 字幕 / EPUB / PDF 均保留。构建 Linux 版本时以本
# 文件内容替换 gui/app.py（导入名保持 gui.app 不变）。
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

# 文件夹批量转换支持的文件类型（设置页类型勾选、文件夹扫描与覆盖检查
# 共用，保持一致）
_FOLDER_FILE_TYPES = ('txt', 'md', 'srt', 'ass', 'ssa', 'lrc',
                      'docx', 'epub', 'pdf')

# 接口协议选项：(配置值, 界面显示名)
_API_PROTOCOLS = [
    ("openai_chat", "OpenAI Chat（chat/completions）"),
    ("openai_responses", "OpenAI Responses（responses）"),
    ("anthropic", "Anthropic（messages）"),
]


def _protocol_label(value: str) -> str:
    for v, label in _API_PROTOCOLS:
        if v == value:
            return label
    return _API_PROTOCOLS[0][1]


def _protocol_value(label: str) -> str:
    for v, l in _API_PROTOCOLS:
        if l == label:
            return v
    return "openai_chat"


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

        # 窗口尺寸与居中：高度不超过屏幕（设置页可竖向滚动，小屏不裁内容）
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        win_h = min(760, max(520, sh - 80))
        x = max(0, (sw - 960) // 2)
        y = max(0, (sh - win_h) // 2)
        self.root.geometry(f"960x{win_h}+{x}+{y}")

        # 图标：Windows 支持 .ico；X11 的 iconbitmap 仅支持 XBM，传 .ico
        # 会抛 TclError，回退用 iconphoto（PIL 加载，PhotoImage 需持引用防 GC）
        icon_path = Path(__file__).resolve().parent.parent / "logo.ico"
        if icon_path.exists():
            try:
                self.root.iconbitmap(default=str(icon_path))
            except tk.TclError:
                try:
                    from PIL import Image, ImageTk
                    with Image.open(icon_path) as im:
                        self._icon_photo = ImageTk.PhotoImage(im)
                    self.root.iconphoto(True, self._icon_photo)
                except Exception:
                    pass  # 图标设置失败不影响功能

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

        # 命名提示随“文件名与原文件名一致”设置更新（见 _update_output_hint）
        self.output_hint = ttk.Label(
            parent,
            text=(
                "文件夹转换且输出目录不同时，输出文件名与原文件名一致"
                if self.config.keep_output_name
                else "输出文件名由转换器自动生成（如 convert_xxx.txt）"
            ),
            foreground="gray",
        )
        self.output_hint.pack(anchor="w", pady=(0, pad))

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

    def _make_scrollable(self, parent: ttk.Frame) -> ttk.Frame:
        """把 parent 变为竖向可滚动区域，返回实际承载内容的内层 frame。

        内容高于可视区域时才显示滚动条；内层宽度跟随画布，fill="x"
        的分组仍占满整行；内容不足一屏时内层撑满画布高度，避免露出
        画布底色（暗色主题下尤其明显）。滚轮接管见 _bind_page_wheel。
        """
        self._scroll_canvas = canvas = tk.Canvas(
            parent, highlightthickness=0, borderwidth=0)
        vbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        inner = ttk.Frame(canvas)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")

        def sync_bar():
            # 内容高于可视区时才显示滚动条
            overflow = inner.winfo_reqheight() > canvas.winfo_height()
            if overflow and vbar.winfo_manager() != "pack":
                vbar.pack(side="right", fill="y")
            elif not overflow and vbar.winfo_manager() == "pack":
                vbar.pack_forget()

        def on_inner_config(_e):
            canvas.configure(scrollregion=canvas.bbox("all"))
            sync_bar()

        def on_canvas_config(e):
            # 内层宽度跟随画布；不足一屏时撑满高度（height=0 表示还原）
            canvas.itemconfigure(win, width=e.width)
            canvas.itemconfigure(
                win, height=e.height if inner.winfo_reqheight() < e.height else 0)
            sync_bar()

        inner.bind("<Configure>", on_inner_config)
        canvas.bind("<Configure>", on_canvas_config)
        return inner

    def _bind_page_wheel(self, root: tk.Widget):
        """root 及其子孙接管鼠标滚轮，滚动 _make_scrollable 建立的画布。

        绑定在控件自身并返回 "break"，优先于组合框等类级滚轮绑定，
        也不必用 bind_all 全局抢占滚轮。指针只会停在已映射的控件上，
        切换到其他选项卡后此处不响应滚轮。
        """
        canvas = self._scroll_canvas

        def on_wheel(e):
            if e.num == 4 or e.delta > 0:
                canvas.yview_scroll(-max(1, abs(e.delta) // 120), "units")
            elif e.num == 5 or e.delta < 0:
                canvas.yview_scroll(max(1, abs(e.delta) // 120), "units")
            return "break"

        def bind_rec(w):
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                w.bind(seq, on_wheel)
            for child in w.winfo_children():
                bind_rec(child)
        bind_rec(root)

    def _build_page_settings(self, parent):
        pad = 5
        outer = parent  # 滚轮绑定需覆盖外层（画布空白处也在设置页内）
        parent = self._make_scrollable(parent)

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

        # 批量转换
        frm = ttk.LabelFrame(parent, text="批量转换", padding=pad)
        frm.pack(fill="x", pady=(0, pad))

        self.folder_scan_var = tk.StringVar(value=self.config.folder_scan_mode)
        ttk.Radiobutton(
            frm, text="选择转换文件夹时，转换文件夹内所有支持的文件类型",
            variable=self.folder_scan_var, value="all",
            command=lambda: [self._update_type_state(), self._save_config()],
        ).pack(anchor="w")
        ttk.Radiobutton(
            frm, text="选择转换文件夹时，只转换指定的文件类型",
            variable=self.folder_scan_var, value="selected",
            command=lambda: [self._update_type_state(), self._save_config()],
        ).pack(anchor="w")

        # 指定类型勾选（仅上一项选中时可勾选）
        type_row = ttk.Frame(frm)
        type_row.pack(anchor="w", padx=(pad * 3, 0))
        self._type_vars = {}
        self._type_buttons = []
        for t in _FOLDER_FILE_TYPES:
            var = tk.BooleanVar(value=t in self.config.folder_file_types)
            btn = ttk.Checkbutton(
                type_row, text=t, variable=var, command=self._save_config)
            btn.pack(side="left", padx=(0, pad))
            self._type_vars[t] = var
            self._type_buttons.append(btn)
        self._update_type_state()

        self.keep_name_var = tk.BooleanVar(value=self.config.keep_output_name)
        ttk.Checkbutton(
            frm, text="文件夹转换且输出路径与输入路径不同时，转换后的文件名与原文件名一致",
            variable=self.keep_name_var, command=self._save_config,
        ).pack(anchor="w", pady=(2, 0))

        frm = ttk.LabelFrame(parent, text="API 配置", padding=pad)
        frm.pack(fill="x", pady=(0, pad))

        self.base_url_var = tk.StringVar(value=self.config.api_base_url)
        self.model_var = tk.StringVar(value=self.config.api_model)
        self.key_var = tk.StringVar(
            value=self.config.api_key if self.config.save_api_key else ""
        )
        self.ctx_window_var = tk.StringVar(value=str(self.config.context_window))
        self.save_key_var = tk.BooleanVar(value=self.config.save_api_key)
        self.protocol_var = tk.StringVar(value=_protocol_label(self.config.api_protocol))

        # 接口协议（第 1 行，只读下拉）
        ttk.Label(frm, text="接口协议：").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Combobox(
            frm, textvariable=self.protocol_var, state="readonly",
            values=[label for _v, label in _API_PROTOCOLS],
        ).grid(row=1, column=1, sticky="we", padx=(pad, 0), pady=2)

        rows = [
            ("Base URL：", self.base_url_var, None),
            ("模型：", self.model_var, None),
            ("API Key：", self.key_var, "*"),
            ("上下文窗口：", self.ctx_window_var, None),
        ]
        for i, (label, var, show) in enumerate(rows, start=2):
            ttk.Label(frm, text=label).grid(row=i, column=0, sticky="w", pady=2)
            entry = ttk.Entry(frm, textvariable=var)
            if show:
                entry.configure(show=show)
            entry.grid(row=i, column=1, sticky="we", padx=(pad, 0), pady=2)
        frm.columnconfigure(1, weight=1)

        ttk.Checkbutton(
            frm, text="记住 API Key",
            variable=self.save_key_var,
        ).grid(row=len(rows) + 2, column=1, sticky="w", pady=(pad, 0))

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
        encodings = ["自动检测", "UTF-8", "GB18030", "GBK", "Big5", "GB2312", "CP950", "UTF-16"]
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

        # 页面内容已建齐，接管滚轮（含后建的各分组控件）
        self._bind_page_wheel(outer)

    def _build_page_about(self, parent):
        info = ttk.Label(
            parent,
            text=(
                "规范繁体字形转换器-AI繁简转换模块\n\n"
                "本模块系“规范繁体字形转换器”的AI转换模块，因系独立构建，亦可脱离主程序单独使用。\n\n"
                "目前只支持繁—简、简—繁转换，对转换中出现的“一对多”情形，调用大模型 API 根据上下文语义判断。\n\n"
                "使用前请在初始设置里选择接口协议（OpenAI Chat / OpenAI Responses / Anthropic），\n"
                "并填写对应的API接口地址、模型名称、API Key，一般AI厂商的API平台所附接口文档会有说明。\n\n"
                "转换效果取决于模型能力，但一般远高于传统转换的正确率。\n\n"
                "虽然转换时只传输极少部分文本至AI厂商服务器，如果不希望文本被泄露，请不要使用本模块转换文件。\n"
                "AI转换过程耗时且需向AI厂商提取支付token费用，不建议转换长文本和大文件。\n\n"
                "模块版本：0.3.3 \n"
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
                # 多扩展名必须用列表（转成 Tcl 列表）："*.a;*.b" 的分号
                # 写法仅 Windows 认，X11 上整串按字面 glob 处理、匹配不到
                # 任何文件；"所有文件" 用 "*"（Unix 上 "*.*" 匹配不到无
                # 扩展名的文件）
                ("所有支持的文件", ("*.txt", "*.md", "*.srt", "*.ass", "*.ssa",
                                  "*.lrc", "*.docx", "*.epub", "*.pdf")),
                ("文本文件", "*.txt"),
                ("Markdown 文件", "*.md"),
                ("字幕文件", ("*.srt", "*.ass", "*.ssa", "*.lrc")),
                ("Word 文档", ("*.docx",)),
                ("EPUB 电子书", "*.epub"),
                ("PDF 文件", "*.pdf"),
                ("所有文件", "*"),
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
        self.config.folder_scan_mode = self.folder_scan_var.get()
        self.config.folder_file_types = [
            t for t in _FOLDER_FILE_TYPES if self._type_vars[t].get()]
        self.config.keep_output_name = self.keep_name_var.get()
        save_config(self.config, DEFAULT_CONFIG_PATH)
        self._update_output_hint()

    def _update_type_state(self):
        # 仅在“只转换指定的文件类型”下可勾选类型
        state = "normal" if self.folder_scan_var.get() == "selected" else "disabled"
        for btn in self._type_buttons:
            btn.configure(state=state)

    def _update_output_hint(self):
        # 开始转换页的命名提示随设置更新
        if self.keep_name_var.get():
            self.output_hint.configure(
                text="文件夹转换且输出目录不同时，输出文件名与原文件名一致")
        else:
            self.output_hint.configure(
                text="输出文件名由转换器自动生成（如 convert_xxx.txt）")

    def _update_config_from_ui(self):
        self.config.api_base_url = self.base_url_var.get().strip()
        self.config.api_model = self.model_var.get().strip()
        self.config.api_key = self.key_var.get().strip()
        self.config.api_protocol = _protocol_value(self.protocol_var.get())
        self.config.save_api_key = self.save_key_var.get()
        try:
            self.config.context_window = int(self.ctx_window_var.get())
        except ValueError:
            self.config.context_window = 10
        save_config(self.config, DEFAULT_CONFIG_PATH)

    # ── 转换 ──────────────────────────────────────────────

    @staticmethod
    def _predict_output_name(input_file: Path, keep_name: bool = False) -> str:
        # 与各转换器的输出命名规则保持一致
        if keep_name:
            # 开启“文件名与原文件名一致”时保留原名
            return input_file.name
        return f"convert_{input_file.name}"

    @staticmethod
    def _keep_original_name(input_file: Path, output_file: Path, log):
        """把 convert_ 前缀的输出改回原文件名（keep_name 命名规则）。

        目标名已存在的情形已在转换前经覆盖预检让用户确认过，因此改名
        采用覆盖语义（replace）；仅在目标被占用、不可写等真正失败时才
        保留 convert_ 前缀名。
        """
        target = output_file.with_name(
            ConverterApp._predict_output_name(input_file, keep_name=True))
        if target == output_file:
            return output_file
        try:
            output_file.replace(target)
        except OSError:
            log(f"无法改名为 {target.name}（目标被占用或不可写），"
                f"保留 {output_file.name}")
            return output_file
        log(f"输出文件名已改回原文件名: {target.name}")
        return target

    def _folder_scan_types(self) -> tuple:
        """文件夹批量转换要扫描的文件类型（按设置页筛选，主线程调用）。"""
        if self.folder_scan_var.get() == "selected":
            return tuple(t for t in _FOLDER_FILE_TYPES if self._type_vars[t].get())
        return _FOLDER_FILE_TYPES

    def _keep_names_enabled(self, input_path: Path, output_dir: Path) -> bool:
        """是否保留原文件名：仅文件夹转换、输出目录与输入目录不同且设置开启。

        就地转换（输出目录=输入目录）仍用 convert_ 前缀，否则会覆盖原
        文件；单文件转换不在此设置的作用范围内。
        """
        return (input_path.is_dir() and self.keep_name_var.get()
                and output_dir.resolve() != input_path.resolve())

    def _collect_folder_files(self, input_path: Path, types=_FOLDER_FILE_TYPES,
                              output_dir: Path = None):
        """递归收集文件夹（含子文件夹）中指定类型的文件。

        返回 (待转换文件列表, 跳过的 convert_ 前缀文件数)；跳过此前
        生成的文件是为了就地转换（输出目录=输入目录）时不反复套娃。
        输出目录嵌套在输入目录内时，一并跳过输出目录下的文件，避免
        上次的输出被再次收集（保留原名模式的输出无 convert_ 前缀，
        仅靠前缀判断不到）；输出目录=输入目录时不排除（就地转换由
        convert_ 前缀跳过兜底）。
        """
        files = []
        for t in types:
            files.extend(f for f in input_path.rglob(f"*.{t}") if f.is_file())
        files.sort(key=lambda p: p.relative_to(input_path).as_posix())
        # 输出目录严格嵌套在输入目录内时，按路径前缀排除其下所有文件
        out_prefix = None
        if output_dir is not None and output_dir.resolve() != input_path.resolve():
            out_prefix = os.path.normcase(str(output_dir.resolve())) + os.sep
        kept = []
        skipped = 0
        for f in files:
            if f.name.startswith("convert_"):
                skipped += 1
            elif out_prefix and os.path.normcase(str(f.resolve())).startswith(out_prefix):
                continue
            else:
                kept.append(f)
        return kept, skipped

    @staticmethod
    def _file_output_dir(input_path: Path, file: Path, output_dir: Path) -> Path:
        # 子文件夹中的文件输出到输出目录下的同名子文件夹，保持目录结构，
        # 也避免不同子文件夹里的同名文件在输出时互相覆盖
        return output_dir / file.parent.relative_to(input_path)

    def _collect_overwrites(self, input_path: Path, output_dir: Path,
                            scan_types=(), keep_names=False):
        """返回本次转换会覆盖的已存在输出文件列表。"""
        if input_path.is_file():
            outputs = [output_dir / self._predict_output_name(input_path, keep_names)]
        else:
            candidates, _skipped = self._collect_folder_files(
                input_path, scan_types, output_dir)
            outputs = [
                self._file_output_dir(input_path, f, output_dir)
                / self._predict_output_name(f, keep_names)
                for f in candidates
            ]
        return [p for p in outputs if p.exists()]

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

        # 在主线程读取 UI 状态（tkinter 非线程安全，worker 线程不直接读取）
        scan_types = self._folder_scan_types() if input_path.is_dir() else ()
        if input_path.is_dir() and not scan_types:
            messagebox.showerror(
                "错误", "“只转换指定的文件类型”未勾选任何类型，请先在初始设置中勾选")
            return
        # 仅文件夹转换：输出目录与输入目录不同且设置开启时，输出改回原文件名
        keep_names = self._keep_names_enabled(input_path, output_dir)

        overwrites = self._collect_overwrites(
            input_path, output_dir, scan_types, keep_names)
        if overwrites:
            # 显示相对输出目录的路径，子文件夹中的文件一目了然
            names = [str(p.relative_to(output_dir)) for p in overwrites]
            preview = "\n".join(names[:5])
            if len(names) > 5:
                preview += f"\n… 等共 {len(names)} 个文件"
            if not messagebox.askyesno(
                "确认覆盖",
                f"输出文件夹中已存在同名文件：\n\n{preview}\n\n是否覆盖？",
            ):
                self._log("已取消转换：输出文件夹中存在同名文件")
                return

        output_dir.mkdir(parents=True, exist_ok=True)

        self._cancel_event.clear()
        self.progress.start()
        self.convert_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")

        # 在主线程读取 UI 状态（tkinter 非线程安全，worker 线程不直接读取）
        enc = self.encoding_var.get()
        options = {
            "force_encoding": None if enc == "自动检测" else enc,
            "preserve_format": self.preserve_fmt_var.get(),
            "convert_footnotes": self.convert_fns_var.get(),
            "keep_output_name": keep_names,
        }

        self._worker_thread = threading.Thread(
            target=self._convert_worker,
            args=(input_path, output_dir, direction, options, scan_types),
        )
        self._worker_thread.daemon = True
        self._worker_thread.start()

    def _cancel_convert(self):
        self._cancel_event.set()
        self._log("正在取消…")

    def _convert_worker(self, input_path, output_dir, direction, options,
                        scan_types=()):
        try:
            if input_path.is_file():
                self._convert_single_file(input_path, output_dir, direction, options)
            elif input_path.is_dir():
                self._convert_folder(input_path, output_dir, direction, options,
                                     scan_types)
            else:
                self.root.after(
                    0, lambda: messagebox.showerror("错误", "输入路径无效")
                )
        except Exception as e:
            err = str(e)
            self.root.after(0, lambda msg=err: messagebox.showerror("错误", msg))
        finally:
            try:
                self.cache.save()
            except Exception as e:
                # 保存失败不应中断 UI 状态恢复（否则进度条会永久卡住）
                self.root.after(0, self._log, f"缓存保存失败: {e}")
            self.root.after(0, self._update_cache_info)
            self.root.after(0, self.progress.stop)
            self.root.after(0, lambda: self.convert_btn.configure(state="normal"))
            self.root.after(0, lambda: self.cancel_btn.configure(state="disabled"))
            self._worker_thread = None

    def _convert_single_file(self, input_path, output_dir, direction, options):
        if self._cancel_event.is_set():
            return
        ext = input_path.suffix.lower()
        log = lambda msg: self.root.after(0, self._log, msg)  # 线程安全
        force_enc = options["force_encoding"]
        preserve_fmt = options["preserve_format"]
        convert_fns = options["convert_footnotes"]

        if ext == '.txt':
            from text_converter import convert_txt_file
            result = convert_txt_file(
                str(input_path), str(output_dir), direction, self.converter,
                force_encoding=force_enc,
                log_callback=log, is_cancelled_callback=self._cancel_event.is_set,
            )
        elif ext == '.md':
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
        elif ext == '.docx':
            from doc_converter import convert_docx_file
            result = convert_docx_file(
                str(input_path), str(output_dir), direction, self.converter,
                preserve_format=preserve_fmt,
                convert_footnotes=convert_fns,
                log_callback=log, is_cancelled_callback=self._cancel_event.is_set,
            )
        elif ext == '.epub':
            from epub_converter import convert_epub_file
            result = convert_epub_file(
                str(input_path), str(output_dir), direction, self.converter,
                log_callback=log, is_cancelled_callback=self._cancel_event.is_set,
            )
        elif ext == '.pdf':
            from pdf_converter import convert_pdf_file
            result = convert_pdf_file(
                str(input_path), str(output_dir), direction, self.converter,
                log_callback=log, is_cancelled_callback=self._cancel_event.is_set,
            )
        else:
            log(f"不支持的文件类型: {input_path.suffix or '（无扩展名）'}，已跳过")
            return

        if self._cancel_event.is_set():
            self.root.after(0, lambda: self._log("已取消"))
        elif result:
            if options.get("keep_output_name"):
                result = self._keep_original_name(input_path, Path(result), log)
            self.root.after(0, lambda r=result: self._log(f"完成：{r}"))
        else:
            self.root.after(0, lambda: self._log("转换失败"))

    def _convert_folder(self, input_path, output_dir, direction, options,
                        scan_types=()):
        files, generated = self._collect_folder_files(
            input_path, scan_types, output_dir)
        if generated:
            self.root.after(0, lambda: self._log(
                f"跳过 {generated} 个此前已生成的 convert_ 前缀文件"))

        if not files:
            msg = "输入文件夹中没有支持的文件"
            if tuple(scan_types) != _FOLDER_FILE_TYPES:
                msg += f"（筛选类型：{'、'.join(scan_types)}）"
            self.root.after(0, lambda m=msg: self._log(m))
            return

        self.root.after(0, lambda: self._log(
            f"共发现 {len(files)} 个文件（含子文件夹）"))
        for f in files:
            if self._cancel_event.is_set():
                self.root.after(0, lambda: self._log("已取消转换"))
                break
            self._convert_single_file(
                f, self._file_output_dir(input_path, f, output_dir),
                direction, options,
            )

    # ── 主循环 ────────────────────────────────────────────

    def run(self):
        self.root.mainloop()
