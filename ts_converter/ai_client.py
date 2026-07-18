"""OpenAI 兼容 API 客户端。

负责构建 prompt、发送请求、解析返回，并处理重试与 fallback。
"""
import json as json_mod
import logging
import re
import time
import warnings
from dataclasses import dataclass

# 导入时临时抑制 requests 版本不匹配警告，之后立即恢复
_original_filters = warnings.filters.copy()
warnings.filterwarnings("ignore", message=".*doesn't match a supported version.*")
import requests  # noqa: E402
warnings.filters = _original_filters

from ts_converter.config import Config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AmbiguityItem:
    """一个歧义字的上下文信息。"""
    context: str
    char: str
    candidates: list[str]


class AIClient:
    """调用 OpenAI 兼容 API 进行歧义字消歧。"""

    def __init__(self, config: Config):
        self.config = config

    # ── Prompt 构建 (JSON 格式) ──────────────────────────

    def _build_prompt_json(self, items: list[AmbiguityItem], direction: str) -> str:
        """构建 JSON 输出格式的 prompt。"""
        if direction == "s2t":
            task = "简体字应转换为哪个繁体字"
        else:
            task = "繁体字应转换为哪个简体字"

        items_desc = []
        for i, item in enumerate(items, 1):
            items_desc.append(
                '  {"id": %d, "context": %s, '
                '"char": %s, '
                '"candidates": %s}'
                % (
                    i,
                    json_mod.dumps(item.context, ensure_ascii=False),
                    json_mod.dumps(item.char, ensure_ascii=False),
                    json_mod.dumps(item.candidates, ensure_ascii=False),
                )
            )
        items_block = ",\n".join(items_desc)

        return (
            f"你是一名专业的中文繁简转换助手。根据每个歧义字的上下文，"
            f"从候选列表中选择正确的字。\n\n"
            f"任务：判断下列{task}。\n"
            f"待判断项：\n[\n{items_block}\n]\n\n"
            f"你必须为上面 {len(items)} 项中的每一项都返回结果，不得省略任何一项。\n"
            f"严格按以下 JSON 格式返回，不要包含任何其他文字：\n"
            f'{{"results": [{{"id": 1, "char": "X"}}, ...]}}\n\n'
            f'其中 "id" 对应输入项的 id，"char" 为所选字。'
        )

    # ── 主流程 ───────────────────────────────────────────

    def resolve(
        self,
        items: list[AmbiguityItem],
        direction: str = "s2t",
        fallback_on_error: bool = True,
        quality_mode: bool = True,
    ) -> list[str]:
        """批量解析歧义字。先尝试 JSON 格式解析，失败时 fallback。

        Args:
            quality_mode: True 时解析不完整会重试剩余项；False 时直接 fallback。
        """
        if not items:
            return []

        prompt = self._build_prompt_json(items, direction)
        payload: dict = {
            "model": self.config.api_model,
            "messages": [
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.config.api_base_url.rstrip('/')}/chat/completions"

        max_retries = self.config.retry_count
        timeout = self.config.api_timeout
        quality_retried = False
        for attempt in range(max_retries):
            try:
                resp = requests.post(
                    url, json=payload, headers=headers, timeout=timeout
                )
                resp.raise_for_status()
                resp_data = resp.json()
                content_text = (
                    resp_data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                if not content_text:
                    raise ValueError("API 返回空内容")

                result, missing = self._parse_response(content_text, items)
                # 质量模式：不完整时重试剩余项（最多一次，独立于网络重试）
                if quality_mode and missing and not quality_retried:
                    quality_retried = True
                    logger.info(
                        "JSON 解析不完整 (%d/%d)，重试剩余 %d 项",
                        len(items) - len(missing), len(items), len(missing),
                    )
                    retry_items = [items[i] for i in missing]
                    retry_result = self._resolve_small_batch(
                        retry_items, direction, payload, headers, url, timeout
                    )
                    for j, idx in enumerate(missing):
                        result[idx] = retry_result[j]
                    return result

                return result
            except Exception as e:
                logger.warning(
                    "API 调用失败 (第 %d/%d 次): %s", attempt + 1, max_retries, e
                )
                if attempt < max_retries - 1:
                    backoff = 2 ** attempt
                    logger.info("等待 %d 秒后重试...", backoff)
                    time.sleep(backoff)
                elif fallback_on_error:
                    logger.warning("所有重试均失败，回退到候选第一个字")
                    return [item.candidates[0] for item in items]
                else:
                    raise

    def _resolve_small_batch(
        self,
        items: list[AmbiguityItem],
        direction: str,
        payload: dict,
        headers: dict,
        url: str,
        timeout: int,
    ) -> list[str]:
        """对少量剩余项发起单独请求（不重试，失败即 fallback）。"""
        try:
            prompt = self._build_prompt_json(items, direction)
            retry_payload = {
                **payload,
                "messages": [{"role": "user", "content": prompt}],
            }
            resp = requests.post(url, json=retry_payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            content_text = (
                resp.json().get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            result, _ = self._parse_response(content_text, items)
            return result
        except Exception:
            return [item.candidates[0] for item in items]

    # ── 响应解析 ─────────────────────────────────────────

    def _parse_response(
        self, content: str, items: list[AmbiguityItem]
    ) -> tuple[list[str], list[int]]:
        """
        解析 AI 返回内容。优先 JSON，失败则尝试逐行正则。

        Returns:
            (result_list, missing_indices): result 与 items 等长；
            missing 为未成功解析的索引列表。
        """
        # 1. 尝试 JSON 解析
        try:
            data = json_mod.loads(content)
            results = data.get("results", []) if isinstance(data, dict) else []
            result_map: dict[int, str] = {}
            for entry in results:
                if not isinstance(entry, dict):
                    continue
                idx = entry.get("id")
                chosen = entry.get("char", "")
                if isinstance(idx, int) and 1 <= idx <= len(items):
                    if chosen in items[idx - 1].candidates:
                        result_map[idx - 1] = chosen
            if result_map:
                result = [result_map.get(i, items[i].candidates[0]) for i in range(len(items))]
                missing = [i for i in range(len(items)) if i not in result_map]
                if missing:
                    logger.warning(
                        "JSON 解析不完整: 期望 %d 项，成功 %d 项",
                        len(items), len(result_map),
                    )
                return result, missing
        except (json_mod.JSONDecodeError, TypeError, KeyError):
            pass

        # 2. JSON 失败时尝试逐行正则
        result = [items[i].candidates[0] for i in range(len(items))]
        parsed = set()
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            # 支持多种格式: "1:字", "1：字", "1.字", "1、字", "1)字"
            match = re.match(r"(\d+)\s*[:：\.\,、\)）]\s*(\S+)", line)
            if not match:
                continue
            idx = int(match.group(1)) - 1
            chosen = match.group(2)
            # 去除尾部标点
            chosen = chosen.rstrip("。,，.、;；!！?？")
            if 0 <= idx < len(items) and chosen in items[idx].candidates:
                result[idx] = chosen
                parsed.add(idx)

        missing = [i for i in range(len(items)) if i not in parsed]
        if missing:
            logger.warning(
                "逐行解析不完整: 期望 %d 项，成功 %d 项，其余使用 fallback",
                len(items), len(items) - len(missing),
            )
        return result, missing


class NoOpAIClient:
    """不调用 AI，总是 fallback 到候选第一个字。仅用于测试。"""

    def resolve(
        self,
        items: list[AmbiguityItem],
        direction: str = "s2t",
        fallback_on_error: bool = True,
        quality_mode: bool = True,
    ) -> list[str]:
        return [item.candidates[0] for item in items]
