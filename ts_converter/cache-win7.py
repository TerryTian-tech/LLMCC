# Win7 兼容版本（Python 3.8）：与 cache.py 逻辑完全一致，仅把类型注解
# 改为 typing 旧式写法（原文件的 dict[str, ...] / str | None 等注解需要
# Python 3.9/3.10，在 Win7 的 3.8 上 import 即崩）。构建 Win7 版本时以
# 本文件内容替换 cache.py（包内导入名保持 ts_converter.cache 不变）。
"""AI 判断结果本地缓存模块。

以 (方向, 上下文, 原字) 为 key 缓存 AI 返回的目标字，
避免对相同上下文重复调用 API。线程安全。
"""
import json
import logging
import threading
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class TranslationCache:
    """持久化的翻译缓存，支持上下文管理器自动保存。线程安全。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: Dict[str, Dict[str, Dict[str, str]]] = {"s2t": {}, "t2s": {}}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    s2t = self._sanitize(data.get("s2t"))
                    t2s = self._sanitize(data.get("t2s"))
                    self._data = {"s2t": s2t, "t2s": t2s}
            except (json.JSONDecodeError, OSError):
                logger.warning("缓存文件损坏，将重新创建")

    @staticmethod
    def _sanitize(section: object) -> Dict[str, Dict[str, str]]:
        """校验嵌套结构，丢弃类型不正确的条目，防止 lookup 时链式 .get 抛错。"""
        if not isinstance(section, dict):
            return {}
        dropped = 0
        result: Dict[str, Dict[str, str]] = {}
        for context, char_map in section.items():
            if not isinstance(context, str) or not isinstance(char_map, dict):
                dropped += 1
                continue
            valid = {
                char: target
                for char, target in char_map.items()
                if isinstance(char, str) and isinstance(target, str)
            }
            dropped += len(char_map) - len(valid)
            if valid:
                result[context] = valid
        if dropped:
            logger.warning("缓存中 %d 条结构异常条目已忽略", dropped)
        return result

    def lookup(self, direction: str, context: str, char: str) -> Optional[str]:
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
            # dumps 在锁内序列化，本身即是对当前数据的原子快照
            text = json.dumps(self._data, ensure_ascii=False, indent=2)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(text, encoding="utf-8")

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
