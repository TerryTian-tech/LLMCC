"""tkinter 图形界面。"""
import logging
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from ts_converter.ai_client import AIClient
from ts_converter.cache import TranslationCache
from ts_converter.config import Config, load_config, save_config
from ts_converter.converter import Converter
from ts_converter.mapping import load_mappings
from ts_converter.utils import process_file

DEFAULT_CONFIG_PATH = Path.home() / ".ts_converter_config.json"
DEFAULT_CACHE_PATH = Path.home() / ".ts_converter_cache.json"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

DIRECTION_LABEL = {"s2t": "繁", "t2s": "简"}


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
        self.root.title("AI繁简转换工具")
        self.root.geometry("680x750")

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
        self.converter = Converter(
            self.mappings, self.ai_client, self.cache, self.config,
            quality_mode=True,
        )

        self._build_ui()

        handler = _GuiLogHandler(self._log, self.root)
        handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
        logging.getLogger("ts_converter").addHandler(handler)
        logging.getLogger("ts_converter").setLevel(logging.INFO)

        self._update_cache_info()

    # ── UI 构建 ──────────────────────────────────────────

    def _build_ui(self):
        padx = 10
        pady = 5

        # ── 输入 ──
        frame_in = tk.LabelFrame(self.root, text="输入", padx=padx, pady=pady)
        frame_in.pack(fill="x", padx=padx, pady=pady)

        tk.Label(frame_in, text="文件/文件夹：").grid(row=0, column=0, sticky="w")
        self.input_var = tk.StringVar()
        tk.Entry(frame_in, textvariable=self.input_var, width=45).grid(
            row=0, column=1, sticky="we"
        )
        tk.Button(frame_in, text="选择文件", command=self._browse_input_file).grid(
            row=0, column=2, padx=2
        )
        tk.Button(frame_in, text="选择文件夹", command=self._browse_input_dir).grid(
            row=0, column=3, padx=2
        )
        frame_in.columnconfigure(1, weight=1)

        # ── 输出 ──
        frame_out = tk.LabelFrame(self.root, text="输出文件夹", padx=padx, pady=pady)
        frame_out.pack(fill="x", padx=padx, pady=pady)

        tk.Label(frame_out, text="文件夹：").grid(row=0, column=0, sticky="w")
        self.output_var = tk.StringVar()
        tk.Entry(frame_out, textvariable=self.output_var, width=45).grid(
            row=0, column=1, sticky="we"
        )
        tk.Button(frame_out, text="选择文件夹", command=self._browse_output_dir).grid(
            row=0, column=2, padx=2
        )
        tk.Label(
            frame_out,
            text="输出文件名自动生成：例如 五帝本纪.txt → 五帝本纪_繁.txt",
            fg="gray",
        ).grid(row=1, column=1, sticky="w", pady=(0, 2))
        frame_out.columnconfigure(1, weight=1)

        # ── 转换方向 ──
        frame_dir = tk.LabelFrame(self.root, text="转换方向", padx=padx, pady=pady)
        frame_dir.pack(fill="x", padx=padx, pady=pady)
        self.direction_var = tk.StringVar(value="s2t")
        tk.Radiobutton(
            frame_dir, text="简 → 繁", variable=self.direction_var, value="s2t"
        ).pack(side="left")
        tk.Radiobutton(
            frame_dir, text="繁 → 简", variable=self.direction_var, value="t2s"
        ).pack(side="left")

        # ── 转换模式 ──
        frame_mode = tk.LabelFrame(self.root, text="转换模式", padx=padx, pady=pady)
        frame_mode.pack(fill="x", padx=padx, pady=pady)
        self.quality_var = tk.BooleanVar(value=True)
        tk.Radiobutton(
            frame_mode, text="质量优先（解析不完整时自动重试，较慢但更准）",
            variable=self.quality_var, value=True,
        ).pack(anchor="w")
        tk.Radiobutton(
            frame_mode, text="速度优先（解析不完整时直接跳过，较快）",
            variable=self.quality_var, value=False,
        ).pack(anchor="w")

        # ── API 配置 ──
        frame_api = tk.LabelFrame(self.root, text="API 配置", padx=padx, pady=pady)
        frame_api.pack(fill="x", padx=padx, pady=pady)

        tk.Label(frame_api, text="Base URL：").grid(row=0, column=0, sticky="w")
        self.base_url_var = tk.StringVar(value=self.config.api_base_url)
        tk.Entry(frame_api, textvariable=self.base_url_var, width=45).grid(
            row=0, column=1, sticky="we"
        )

        tk.Label(frame_api, text="模型：").grid(row=1, column=0, sticky="w")
        self.model_var = tk.StringVar(value=self.config.api_model)
        tk.Entry(frame_api, textvariable=self.model_var, width=45).grid(
            row=1, column=1, sticky="we"
        )

        tk.Label(frame_api, text="API Key：").grid(row=2, column=0, sticky="w")
        self.key_var = tk.StringVar(
            value=self.config.api_key if self.config.save_api_key else ""
        )
        tk.Entry(frame_api, textvariable=self.key_var, width=45, show="*").grid(
            row=2, column=1, sticky="we"
        )

        self.save_key_var = tk.BooleanVar(value=self.config.save_api_key)
        tk.Checkbutton(
            frame_api,
            text="记住 API Key（明文保存，存在安全风险）",
            variable=self.save_key_var,
        ).grid(row=3, column=1, sticky="w")

        tk.Label(frame_api, text="上下文窗口：").grid(row=4, column=0, sticky="w")
        self.ctx_window_var = tk.StringVar(value=str(self.config.context_window))
        tk.Spinbox(
            frame_api,
            textvariable=self.ctx_window_var,
            from_=5, to=200, increment=5, width=6,
        ).grid(row=4, column=1, sticky="w")

        frame_api.columnconfigure(1, weight=1)

        # ── 缓存信息 ──
        frame_cache = tk.LabelFrame(self.root, text="缓存", padx=padx, pady=pady)
        frame_cache.pack(fill="x", padx=padx, pady=pady)

        self.cache_info_var = tk.StringVar(value="加载中…")
        tk.Label(frame_cache, textvariable=self.cache_info_var).pack(
            side="left", padx=padx
        )
        tk.Button(
            frame_cache, text="清空缓存", command=self._clear_cache
        ).pack(side="right", padx=padx)

        # ── 执行按钮 ──
        frame_action = tk.Frame(self.root)
        frame_action.pack(fill="x", padx=padx, pady=pady)

        self.convert_btn = tk.Button(
            frame_action, text="开始转换", command=self._start_convert
        )
        self.convert_btn.pack(side="left", padx=5)

        self.cancel_btn = tk.Button(
            frame_action, text="取消转换", command=self._cancel_convert, state="disabled"
        )
        self.cancel_btn.pack(side="left", padx=5)

        self.progress = ttk.Progressbar(frame_action, mode="indeterminate")
        self.progress.pack(side="left", fill="x", expand=True, padx=5)

        # ── 日志 ──
        frame_log = tk.LabelFrame(self.root, text="日志", padx=padx, pady=pady)
        frame_log.pack(fill="both", expand=True, padx=padx, pady=pady)
        self.log_box = scrolledtext.ScrolledText(
            frame_log, state="disabled", height=8
        )
        self.log_box.pack(fill="both", expand=True)

    # ── 日志 ──────────────────────────────────────────────

    def _log(self, message: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", message + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    # ── 浏览 ──────────────────────────────────────────────

    def _browse_input_file(self):
        path = filedialog.askopenfilename(filetypes=[("Text files", "*.txt")])
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
        """统计缓存总条数。"""
        return self.cache.entry_count()

    def _clear_cache(self):
        if not messagebox.askyesno("确认", "确定要清空所有本地缓存吗？"):
            return
        self.cache.clear()
        self.cache.save()
        self._update_cache_info()
        self._log("已清空本地缓存")

    # ── 配置 ──────────────────────────────────────────────

    def _update_config_from_ui(self):
        self.config.api_base_url = self.base_url_var.get().strip()
        self.config.api_model = self.model_var.get().strip()
        self.config.api_key = self.key_var.get().strip()
        self.config.save_api_key = self.save_key_var.get()
        try:
            self.config.context_window = int(self.ctx_window_var.get())
        except ValueError:
            self.config.context_window = 30
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

    def _make_output_name(self, input_file: Path, direction: str) -> str:
        """生成输出文件名：五帝本纪.txt → 五帝本纪_繁.txt"""
        suffix = DIRECTION_LABEL.get(direction, direction)
        return f"{input_file.stem}_{suffix}{input_file.suffix}"

    def _convert_worker(
        self, input_path: Path, output_dir: Path, direction: str
    ):
        try:
            if input_path.is_file():
                out_file = output_dir / self._make_output_name(input_path, direction)
                self._log(f"开始转换：{input_path.name} → {out_file.name}")
                process_file(input_path, out_file, self.converter, direction)
                self.root.after(0, lambda: self._log(f"完成：{out_file}"))
            elif input_path.is_dir():
                txt_files = sorted(input_path.glob("*.txt"))
                if not txt_files:
                    self.root.after(
                        0, lambda: self._log("输入文件夹中没有 .txt 文件")
                    )
                    return
                for txt_file in txt_files:
                    if self._cancel_event.is_set():
                        self.root.after(0, lambda: self._log("已取消转换"))
                        break
                    out_file = output_dir / self._make_output_name(txt_file, direction)
                    self.root.after(
                        0,
                        lambda f=txt_file, o=out_file: self._log(
                            f"开始转换：{f.name} → {o.name}"
                        ),
                    )
                    process_file(txt_file, out_file, self.converter, direction)
                    self.root.after(
                        0, lambda o=out_file: self._log(f"完成：{o}")
                    )
            else:
                self.root.after(
                    0, lambda: messagebox.showerror("错误", "输入路径无效")
                )
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("错误", str(e)))
        finally:
            self.cache.save()
            self.root.after(0, self._update_cache_info)
            self.root.after(0, self.progress.stop)
            self.root.after(0, lambda: self.convert_btn.configure(state="normal"))
            self.root.after(0, lambda: self.cancel_btn.configure(state="disabled"))
            self._worker_thread = None

    def _update_cache_info(self):
        count = self._count_cache_entries()
        self.cache_info_var.set(f"本地缓存：{count} 条")

    # ── 主循环 ────────────────────────────────────────────

    def run(self):
        self.root.mainloop()
