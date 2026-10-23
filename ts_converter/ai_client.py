"""AI API 客户端，支持 OpenAI Chat（chat/completions）、OpenAI Responses
（responses）与 Anthropic（messages）三种协议，由 Config.api_protocol 选择。

负责构建 prompt、发送请求、解析返回，并处理重试与 fallback。
"""
import json as json_mod
import logging
import re
import time
import warnings
from dataclasses import dataclass
from typing import Callable

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

    # ── 协议适配（OpenAI Chat / OpenAI Responses / Anthropic） ──

    def _request_spec(self, prompt: str) -> tuple[str, dict, dict, str | None]:
        """按配置的 api_protocol 构造 (url, headers, payload, JSON格式参数名)。

        - openai_chat:      POST {base}/chat/completions，response_format 指定 JSON 模式
        - openai_responses: POST {base}/responses，text.format 指定 JSON 模式
        - anthropic:        POST {base}/v1/messages（base 末尾无 /v1 时自动补上），
                            x-api-key 鉴权，无 JSON 模式参数（靠 prompt 约束）

        返回的 JSON 格式参数名供 400 降级时移除；Anthropic 返回 None。
        """
        protocol = getattr(self.config, "api_protocol", "openai_chat") or "openai_chat"
        base = self.config.api_base_url.rstrip("/")
        model = self.config.api_model
        if protocol == "anthropic":
            if not base.endswith("/v1"):
                base += "/v1"
            url = base + "/messages"
            headers = {
                "x-api-key": self.config.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            }
            payload: dict = {
                "model": model,
                "max_tokens": 4096,  # Anthropic 必填；批量消歧返回很小，取宽裕值
                "temperature": 0.0,
                "messages": [{"role": "user", "content": prompt}],
            }
            return url, headers, payload, None
        if protocol == "openai_responses":
            url = base + "/responses"
            headers = {
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": model,
                "input": prompt,
                "temperature": 0.0,
                "text": {"format": {"type": "json_object"}},
            }
            return url, headers, payload, "text"
        url = base + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
        return url, headers, payload, "response_format"

    def _extract_content(self, resp_data: dict) -> str:
        """从各协议的响应 JSON 中取出文本内容，取不到返回空串。"""
        protocol = getattr(self.config, "api_protocol", "openai_chat") or "openai_chat"
        if protocol == "openai_responses":
            # Responses API：output 为消息列表，文本在 content 的 output_text
            # 块；部分代理/新版直接提供 output_text 汇总字段
            direct = resp_data.get("output_text")
            if isinstance(direct, str) and direct:
                return direct
            parts = []
            for item in resp_data.get("output") or []:
                if not isinstance(item, dict) or item.get("type") != "message":
                    continue
                for block in item.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "output_text":
                        parts.append(block.get("text") or "")
            return "".join(parts)
        if protocol == "anthropic":
            parts = []
            for block in resp_data.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text") or "")
            return "".join(parts)
        # openai_chat
        choices = resp_data.get("choices") or [{}]
        first = choices[0] if isinstance(choices[0], dict) else {}
        return ((first.get("message") or {}).get("content")) or ""

    # ── 主流程 ───────────────────────────────────────────

    def resolve(
        self,
        items: list[AmbiguityItem],
        direction: str = "s2t",
        fallback_on_error: bool = True,
        quality_mode: bool = True,
        is_cancelled_callback: Callable[[], bool] | None = None,
    ) -> list[str | None]:
        """批量解析歧义字。先尝试 JSON 格式解析，失败时 fallback。

        Returns:
            与 items 等长的列表；None 表示该项未能可靠解析（调用方应
            使用候选字 fallback，且不得写入缓存）。当 fallback_on_error
            为 False 时失败直接抛出异常（用户取消除外，取消始终返回
            全 None 列表）。

        Args:
            quality_mode: True 时解析不完整会重试剩余项；False 时直接 fallback。
            is_cancelled_callback: 取消检查回调；重试退避期间会分片检查，
                保证取消操作能及时中断等待。
        """
        if not items:
            return []

        prompt = self._build_prompt_json(items, direction)
        url, headers, payload, format_key = self._request_spec(prompt)
        # 400 时可渐进移除的可选参数（部分端点不支持 JSON 模式参数或
        # temperature），每次 400 移除一个后用剩余次数重试
        droppable = ([format_key] if format_key else []) + ["temperature"]

        max_retries = self.config.retry_count
        timeout = self.config.api_timeout
        quality_retried = False
        for attempt in range(max_retries):
            if is_cancelled_callback and is_cancelled_callback():
                logger.info("已取消，停止 API 请求")
                return [None] * len(items)
            try:
                resp = requests.post(
                    url, json=payload, headers=headers, timeout=timeout
                )
                resp.raise_for_status()
                resp_data = resp.json()
                content_text = self._extract_content(resp_data)
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
                        retry_items, direction, timeout,
                        include_json_format=format_key in droppable,
                    )
                    for j, idx in enumerate(missing):
                        result[idx] = retry_result[j]
                    return result

                return result
            except Exception as e:
                logger.warning(
                    "API 调用失败 (第 %d/%d 次): %s", attempt + 1, max_retries, e
                )
                # 部分兼容端点不支持 JSON 模式参数或 temperature（直接返回
                # 400），遇到后逐个移除可选参数用剩余次数重试
                if (
                    isinstance(e, requests.HTTPError)
                    and e.response is not None
                    and e.response.status_code == 400
                    and droppable
                ):
                    key = droppable.pop(0)
                    payload.pop(key, None)
                    logger.warning("端点可能不支持 %s 参数，已移除并重试", key)
                if attempt < max_retries - 1:
                    backoff = 2 ** attempt
                    logger.info("等待 %d 秒后重试...", backoff)
                    if is_cancelled_callback:
                        # 分片睡眠，使取消请求能及时中断退避等待
                        slept = 0.0
                        while slept < backoff and not is_cancelled_callback():
                            step = min(0.25, backoff - slept)
                            time.sleep(step)
                            slept += step
                    else:
                        time.sleep(backoff)
                elif fallback_on_error:
                    logger.warning("所有重试均失败，回退到候选第一个字")
                    return [None] * len(items)
                else:
                    raise

    def _resolve_small_batch(
        self,
        items: list[AmbiguityItem],
        direction: str,
        timeout: int,
        include_json_format: bool = True,
    ) -> list[str | None]:
        """对少量剩余项发起单独请求（不重试，失败即 fallback）。

        返回值中 None 表示该项未能可靠解析。include_json_format 为 False
        时沿用主请求已降级的状态（不携带 JSON 格式参数）。
        """
        try:
            prompt = self._build_prompt_json(items, direction)
            url, headers, payload, format_key = self._request_spec(prompt)
            if not include_json_format and format_key:
                payload.pop(format_key, None)
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            content_text = self._extract_content(resp.json())
            result, _ = self._parse_response(content_text, items)
            return result
        except Exception:
            return [None] * len(items)

    # ── 响应解析 ─────────────────────────────────────────

    def _parse_response(
        self, content: str, items: list[AmbiguityItem]
    ) -> tuple[list[str], list[int]]:
        """
        解析 AI 返回内容。优先 JSON，失败则尝试逐行正则。

        Returns:
            (result_list, missing_indices): result 与 items 等长，未解析项
            为 None；missing 为未成功解析的索引列表。
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
                result = [result_map.get(i) for i in range(len(items))]
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
        result: list[str | None] = [None] * len(items)
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
    """不调用 AI，全部返回 None（走候选字 fallback，不写缓存）。仅用于测试。"""

    def resolve(
        self,
        items: list[AmbiguityItem],
        direction: str = "s2t",
        fallback_on_error: bool = True,
        quality_mode: bool = True,
        is_cancelled_callback: Callable[[], bool] | None = None,
    ) -> list[str | None]:
        return [None] * len(items)
