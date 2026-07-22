"""文本处理工具函数。"""
from pathlib import Path


def extract_context(text: str, index: int, window: int = 30) -> str:
    """提取指定位置前后 window 个字符作为上下文窗口。"""
    start = max(0, index - window)
    end = min(len(text), index + window + 1)
    return text[start:end]


def split_text_into_chunks(text: str, max_chunk_size: int = 10000) -> list[str]:
    """按段落分块，超长段落按字数强制切割。"""
    if len(text) <= max_chunk_size:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for para in text.split("\n"):
        para_len = len(para) + 1  # +1 for newline
        # 如果当前块装满，先提交
        if current_len + para_len > max_chunk_size and current:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        # 如果单个段落超过分块上限，强制切割
        while len(para) > max_chunk_size and not current:
            chunks.append(para[:max_chunk_size])
            para = para[max_chunk_size:]
        current.append(para)
        current_len += len(para) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks