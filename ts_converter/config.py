"""配置读写模块。

默认配置文件位于用户主目录的 .ts_converter_config.json。
"""
import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class Config:
    api_base_url: str = "https://api.openai.com/v1"
    api_model: str = "gpt-4o-mini"
    api_key: str = ""
    context_window: int = 30
    batch_size: int = 8
    save_api_key: bool = False
    retry_count: int = 3
    on_api_error: str = "fallback"
    api_timeout: int = 60

    def __post_init__(self):
        if self.batch_size < 1:
            raise ValueError(f"batch_size 必须 >= 1，当前值: {self.batch_size}")
        if self.context_window < 1:
            raise ValueError(f"context_window 必须 >= 1，当前值: {self.context_window}")
        if self.retry_count < 1:
            raise ValueError(f"retry_count 必须 >= 1，当前值: {self.retry_count}")
        if self.api_timeout < 1:
            raise ValueError(f"api_timeout 必须 >= 1，当前值: {self.api_timeout}")
        if self.on_api_error not in ("fallback", "abort"):
            raise ValueError(
                f"on_api_error 必须为 'fallback' 或 'abort'，当前值: {self.on_api_error}"
            )


DEFAULT_CONFIG = Config()


def load_config(path: Path) -> Config:
    """从 JSON 文件加载配置；文件不存在或损坏时返回默认配置。"""
    path = Path(path)
    if not path.exists():
        return Config()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return Config()
    valid_fields = {f for f in Config.__dataclass_fields__}
    filtered: dict = {}
    for k, v in data.items():
        if k not in valid_fields:
            continue
        # 类型强制转换
        expected_type = Config.__dataclass_fields__[k].type
        if expected_type is int and not isinstance(v, int):
            try:
                v = int(v)
            except (ValueError, TypeError):
                continue
        elif expected_type is bool and not isinstance(v, bool):
            if isinstance(v, str):
                v = v.lower() in ("true", "1", "yes")
            else:
                v = bool(v)
        filtered[k] = v
    try:
        return Config(**filtered)
    except (TypeError, ValueError):
        return Config()


def save_config(config: Config, path: Path) -> None:
    """保存配置到 JSON 文件。若 save_api_key 为 False 则不保存 api_key。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(config)
    if not config.save_api_key:
        data["api_key"] = ""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)  # 限制为仅文件所有者可读写
    except OSError:
        pass  # Windows 上可能不可用，忽略
