"""繁简/简繁转换核心模块。

流程：
1. 在原始文本上扫描一对多歧义字，检查缓存
2. 对缓存未命中的批量请求 AI
3. 在字符级别应用转换（AI 结果 + 一对一查表）
"""
import logging
import threading
from typing import Optional

from ts_converter.ai_client import AIClient, AmbiguityItem
from ts_converter.cache import TranslationCache
from ts_converter.config import Config
from ts_converter.mapping import MappingTables
from ts_converter.utils import extract_context

logger = logging.getLogger(__name__)


class Converter:
    """繁简/简繁转换器，结合本地字表和 AI 语义判断。"""

    def __init__(
        self,
        mappings: MappingTables,
        ai_client: AIClient,
        cache: TranslationCache,
        config: Optional[Config] = None,
        quality_mode: bool = True,
        cancel_event: Optional["threading.Event"] = None,
    ):
        self.mappings = mappings
        self.ai_client = ai_client
        self.cache = cache
        self.config = config or Config()
        self.quality_mode = quality_mode
        self._cancel_event = cancel_event

    def convert(self, text: str, direction: str) -> str:
        if direction not in ("s2t", "t2s"):
            raise ValueError(f"direction 必须为 's2t' 或 't2s'，当前值: {direction}")
        if direction == "s2t":
            one_map = self.mappings.s2t_one
            many_map = self.mappings.s2t_many
        else:
            one_map = self.mappings.t2s_one
            many_map = self.mappings.t2s_many

        # 1. 扫描歧义字（在原始文本上）
        ambiguous: list[tuple[int, str, str]] = []  # (index, char, context)
        for i, ch in enumerate(text):
            if ch in many_map:
                ctx = extract_context(text, i, self.config.context_window)
                cached = self.cache.lookup(direction, ctx, ch)
                if cached is not None:
                    continue  # 缓存命中，跳过
                ambiguous.append((i, ch, ctx))

        # 2. 批量请求 AI
        batch_size = self.config.batch_size
        ai_results: dict[tuple[str, str], str] = {}  # (ctx, char) -> target
        for start in range(0, len(ambiguous), batch_size):
            if self._cancel_event and self._cancel_event.is_set():
                logger.info("转换已被取消，剩余歧义字使用 fallback")
                break
            batch = ambiguous[start : start + batch_size]
            items = [
                AmbiguityItem(context=ctx, char=ch, candidates=many_map[ch])
                for _, ch, ctx in batch
            ]
            resolved = self.ai_client.resolve(
                items,
                direction=direction,
                fallback_on_error=(self.config.on_api_error == "fallback"),
                quality_mode=self.quality_mode,
            )
            for (_, ch, ctx), target in zip(batch, resolved):
                self.cache.store(direction, ctx, ch, target)
                ai_results[(ctx, ch)] = target

        # 3. 应用转换
        chars = list(text)
        for i, ch in enumerate(chars):
            if ch in many_map:
                ctx = extract_context(text, i, self.config.context_window)
                # 优先检查本次转换的 AI 结果（防缓存被外部清空）
                local = ai_results.get((ctx, ch))
                if local is not None:
                    chars[i] = local
                else:
                    cached = self.cache.lookup(direction, ctx, ch)
                    if cached is not None:
                        chars[i] = cached
                    else:
                        chars[i] = many_map[ch][0] if many_map.get(ch) else ch  # fallback
            elif ch in one_map:
                chars[i] = one_map[ch]
        return "".join(chars)
