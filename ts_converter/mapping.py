"""繁简转换映射表加载模块。

从 data/ 目录加载四个 OpenCC 格式的映射文件。
"""
from collections import namedtuple
from pathlib import Path

MappingTables = namedtuple(
    "MappingTables", ["s2t_one", "s2t_many", "t2s_one", "t2s_many"]
)


def _load_one_to_one(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            result[parts[0]] = parts[1]
    return result


def _load_one_to_many(path: Path) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
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
