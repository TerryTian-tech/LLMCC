# Win7 兼容版本（Python 3.8）：与 mapping.py 逻辑完全一致，仅把类型注解
# 改为 typing 旧式写法（原文件的 dict[str, ...] 注解需要 Python 3.9，
# 在 Win7 的 3.8 上 import 即崩）。构建 Win7 版本时以本文件内容替换
# mapping.py（包内导入名保持 ts_converter.mapping 不变）。
"""繁简转换映射表加载模块。

从 data/ 目录加载四个 OpenCC 格式的映射文件。
"""
from collections import namedtuple
from pathlib import Path
from typing import Dict, List

MappingTables = namedtuple(
    "MappingTables", ["s2t_one", "s2t_many", "t2s_one", "t2s_many"]
)


def _load_one_to_one(path: Path) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for line in path.read_text(encoding="UTF-8-SIG").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            result[parts[0]] = parts[1]
    return result


def _load_one_to_many(path: Path) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    for line in path.read_text(encoding="UTF-8-SIG").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1:]:
            result[parts[0]] = parts[1:]
    return result


def load_mappings(data_dir: Path) -> MappingTables:
    """从指定目录加载四个映射表。

    缺失任一文件时抛出 FileNotFoundError 并给出中文提示。
    """
    data_dir = Path(data_dir)
    required_files = [
        "STCharacters-1.txt", "STCharacters-2.txt",
        "TSCharacters-1.txt", "TSCharacters-2.txt",
    ]
    missing = [f for f in required_files if not (data_dir / f).exists()]
    if missing:
        raise FileNotFoundError(
            f"映射文件缺失，请确保以下文件存在于 {data_dir} 目录下：\n"
            + "\n".join(f"  - {f}" for f in missing)
        )
    return MappingTables(
        s2t_one=_load_one_to_one(data_dir / "STCharacters-1.txt"),
        s2t_many=_load_one_to_many(data_dir / "STCharacters-2.txt"),
        t2s_one=_load_one_to_one(data_dir / "TSCharacters-1.txt"),
        t2s_many=_load_one_to_many(data_dir / "TSCharacters-2.txt"),
    )
