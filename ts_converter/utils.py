"""文本处理工具函数。"""


def extract_context(text: str, index: int, window: int = 30) -> str:
    """提取指定位置前后 window 个字符作为上下文窗口。"""
    start = max(0, index - window)
    end = min(len(text), index + window + 1)
    return text[start:end]
