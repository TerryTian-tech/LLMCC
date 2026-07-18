"""AI 判断结果本地缓存模块。

以 (方向, 上下文, 原字) 为 key 缓存 AI 返回的目标字，
避免对相同上下文重复调用 API。线程安全。
"""
import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


class TranslationCache:
    """持久化的翻译缓存，支持上下文管理器自动保存。线程安全。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, dict[str, str]]] = {"s2t": {}, "t2s": {}}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    s2t = data.get("s2t", {})
                    t2s = data.get("t2s", {})
                    self._data = {
                        "s2t": s2t if isinstance(s2t, dict) else {},
                        "t2s": t2s if isinstance(t2s, dict) else {},
                    }
            except (json.JSONDecodeError, OSError):
                logger.warning("缓存文件损坏，将重新创建")

    def lookup(self, direction: str, context: str, char: str) -> str | None:
        """查找缓存，未命中返回 None。"""
        with self._lock:
            return self._data.get(direction, {}).get(context, {}).get(char)

    def store(self, direction: str, context: str, char: str, target: str) -> None:
        """存入缓存。安全增量写入，不会覆盖同一 context 下的其他条目。"""
        with self._lock:
            if direction not in self._data:
                self._data[direction] = {}
            if context not in self._data[direction]:
                self._data[direction][context] = {}
            self._data[direction][context][char] = target

    def save(self) -> None:
        """持久化到 JSON 文件。"""
        with self._lock:
            data_copy = json.loads(
                json.dumps(self._data, ensure_ascii=False)
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data_copy, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def entry_count(self) -> int:
        """返回缓存总条数。"""
        with self._lock:
            total = 0
            for direction in ("s2t", "t2s"):
                for ctx_map in self._data.get(direction, {}).values():
                    total += len(ctx_map)
            return total

    def clear(self) -> None:
        """清空所有缓存。"""
        with self._lock:
            self._data = {"s2t": {}, "t2s": {}}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.save()
