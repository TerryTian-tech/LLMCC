"""
文本文件转换模块 —— txt / srt / ass / lrc 字幕文件。

依赖 ts_converter 库（AI 繁简转换），保留编码自动检测能力。
"""
import os
import re
import chardet


def detect_encoding(file_path, log_callback=None, force_encoding=None):
    """检测文件编码，特别处理中文 ANSI 编码"""
    def log(msg):
        if log_callback:
            log_callback(msg)

    if force_encoding:
        log(f"用户强制指定编码: {force_encoding}")
        return force_encoding

    log(f"检测文件编码: {file_path}")
    with open(file_path, 'rb') as f:
        raw_data = f.read()

    result = chardet.detect(raw_data)
    encoding = result['encoding']
    confidence = result['confidence']
    log(f"chardet检测结果: {encoding} (置信度: {confidence})")

    if encoding == 'GB2312' and confidence < 0.95:
        try:
            raw_data.decode('gb18030', errors='strict')
            log("使用GB18030编码以确保兼容性")
            return 'gb18030'
        except UnicodeDecodeError:
            pass

    if confidence < 0.7 or encoding in ['ISO-8859-1', 'Windows-1252', 'ascii']:
        chinese_encodings = ['gb18030', 'gbk', 'gb2312', 'big5']
        for enc in chinese_encodings:
            try:
                decoded = raw_data.decode(enc, errors='strict')
                has_chinese = any(
                    '\u4e00' <= char <= '\u9fff'
                    or '\u3400' <= char <= '\u4dbf'
                    or '\u3000' <= char <= '\u303f'
                    or '\uff00' <= char <= '\uffef'
                    for char in decoded
                )
                if has_chinese:
                    log(f"检测到中文字符，使用编码: {enc}")
                    return enc
            except UnicodeDecodeError:
                continue

        for enc in ['gb18030', 'gbk']:
            try:
                decoded = raw_data.decode(enc, errors='replace')
                has_chinese = any(
                    '\u4e00' <= char <= '\u9fff'
                    or '\u3400' <= char <= '\u4dbf'
                    or '\u3000' <= char <= '\u303f'
                    or '\uff00' <= char <= '\uffef'
                    for char in decoded
                )
                if has_chinese:
                    replaced_count = decoded.count('\ufffd')
                    if replaced_count == 0:
                        log(f"宽松模式下检测到中文且无替换字符，使用编码: {enc}")
                        return enc
                    else:
                        ratio = replaced_count / len(decoded) if decoded else 1
                        if ratio < 0.005:
                            log(f"宽松模式下检测到中文（替换率{ratio:.4%}极低），使用编码: {enc}")
                            return enc
            except Exception:
                continue

    if encoding == 'utf-8' and confidence < 0.9:
        try:
            decoded = raw_data.decode('gb18030', errors='strict')
            if any('\u4e00' <= char <= '\u9fff' for char in decoded):
                log("检测到GB18030编码的中文字符，使用GB18030编码")
                return 'gb18030'
        except UnicodeDecodeError:
            pass

    if not encoding:
        encoding = 'utf-8'
    if encoding.lower() in ['gb2312', 'gbk']:
        log(f"将{encoding}升级为GB18030以确保更好的兼容性")
        return 'gb18030'
    if encoding.lower() not in ['utf-8', 'utf-8-sig', 'gb18030', 'gbk', 'gb2312', 'big5']:
        if confidence < 0.5:
            log(f"chardet检测到非中文编码'{encoding}'（置信度{confidence:.4%}），回退到GB18030")
            return 'gb18030'
    return encoding


def safe_read_file(file_path, encoding, log_callback=None):
    """安全读取文件，处理编码问题"""
    def log(msg):
        if log_callback:
            log_callback(msg)

    if encoding.lower() in ['gb2312', 'gbk']:
        try:
            with open(file_path, 'r', encoding='gb18030', errors='strict') as f:
                return f.read()
        except UnicodeDecodeError as e:
            log(f"GB18030严格模式读取失败: {e}")

    try:
        with open(file_path, 'r', encoding=encoding, errors='strict') as f:
            return f.read()
    except UnicodeDecodeError:
        log("使用严格模式读取失败，尝试忽略错误字符")
        try:
            with open(file_path, 'r', encoding=encoding, errors='ignore') as f:
                content = f.read()
                if any('\u4e00' <= char <= '\u9fff' for char in content):
                    return content
                log("读取内容不包含中文字符，尝试GB18030编码")
                with open(file_path, 'r', encoding='gb18030', errors='ignore') as f2:
                    return f2.read()
        except Exception as e:
            log(f"读取文件时发生错误: {e}")
            try:
                with open(file_path, 'r', encoding='gb18030', errors='ignore') as f:
                    return f.read()
            except Exception as e2:
                log(f"最终读取失败: {e2}")
                return ""


# ── SRT 字幕 ──────────────────────────────────────────

def _collect_srt_texts(content: str) -> tuple[list[list[str]], list]:
    """解析 SRT，收集所有字幕文本行，返回 (text_blocks, block_templates)。"""
    lines = content.split('\n')
    header_lines: list[str] = []      # 序号+时间码行
    text_blocks: list[list[str]] = []  # 每个字幕块的文本行集合
    block_templates: list[list[str | tuple]] = []  # 重建模板
    i = 0

    while i < len(lines):
        line = lines[i]
        if line.strip().isdigit() and i + 1 < len(lines) and '-->' in lines[i + 1]:
            # 序号行
            seq_line = line
            i += 1
            time_line = lines[i]
            i += 1

            block_texts: list[str] = []
            while i < len(lines):
                text_line = lines[i]
                if text_line.strip() == '':
                    i += 1
                    break
                if text_line.strip().isdigit() and i + 1 < len(lines) and '-->' in lines[i + 1]:
                    break
                block_texts.append(text_line)
                i += 1

            if block_texts:
                text_blocks.append(block_texts)
                block_templates.append(('block', len(text_blocks) - 1, seq_line, time_line))
            else:
                block_templates.append(('raw', seq_line, time_line))
        else:
            block_templates.append(('raw', line))
            i += 1

    return text_blocks, block_templates


def _reassemble_srt(text_blocks: list[list[str]], block_templates: list) -> list[str]:
    """用转换后的 text_blocks 重建 SRT 行"""
    result = []
    tb_idx = 0
    for item in block_templates:
        if item[0] == 'block':
            _, _, seq_line, time_line = item
            result.append(seq_line)
            result.append(time_line)
            for t in text_blocks[tb_idx]:
                result.append(t)
            tb_idx += 1
        elif item[0] == 'raw':
            _, line = item
            result.append(line)
        else:
            result.append(item)
    return result


def convert_srt_file(input_path, output_folder, direction, converter,
                     log_callback=None, is_cancelled_callback=None,
                     force_encoding=None):
    """将 SRT 字幕文件进行繁简转换（批量模式）"""
    def log(msg):
        if log_callback:
            log_callback(msg)

    if is_cancelled_callback and is_cancelled_callback():
        return False

    try:
        if not os.path.exists(input_path):
            log(f"错误：文件不存在 - {input_path}")
            return False
        os.makedirs(output_folder, exist_ok=True)
        log(f"正在处理SRT字幕文件: {os.path.basename(input_path)}")

        encoding = detect_encoding(input_path, log_callback, force_encoding)
        content = safe_read_file(input_path, encoding, log_callback)

        if is_cancelled_callback and is_cancelled_callback():
            return False

        text_blocks, block_templates = _collect_srt_texts(content)

        if text_blocks:
            # 收集所有纯文本（去除 ASS 标签）
            # \0 分隔同行的标签段，\1 分隔不同行
            line_texts: list[str] = []
            tag_maps: list[list[str | None]] = []

            for block in text_blocks:
                for line in block:
                    plain_parts, tag_parts = _split_tags(line)
                    line_texts.append('\0'.join(plain_parts))
                    tag_maps.append(tag_parts)

            big_text = '\1'.join(line_texts)
            converted_big = converter.convert(big_text, direction)
            converted_lines = converted_big.split('\1')

            # 重建每行（合并回标签）
            for i, (block, li) in enumerate(((bi, li) for bi, block in enumerate(text_blocks) for li in range(len(block)))):
                text_blocks[block][li] = _rejoin_tags(converted_lines[i], tag_maps[i])

        result_lines = _reassemble_srt(text_blocks, block_templates)

        output_filename = f"convert_{os.path.basename(input_path)}"
        output_path = os.path.join(output_folder, output_filename)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(result_lines))
        log(f"已保存: {output_path}")
        return output_path
    except Exception as e:
        log(f"处理SRT字幕文件 {input_path} 时出错: {str(e)}")
        return False


# ── 标签处理工具 ──────────────────────────────────────

_TAG_RE = re.compile(r'\{[^}]*\}')


def _split_tags(text: str) -> tuple[list[str], list[str | None]]:
    """将文本拆分为 (纯文本片段, 标签列表)。标签位置用 None 占位。"""
    plain_parts: list[str] = []
    tag_parts: list[str | None] = []
    last_end = 0
    for match in _TAG_RE.finditer(text):
        plain_parts.append(text[last_end:match.start()])
        tag_parts.append(None)      # 纯文本占位
        tag_parts.append(match.group())  # 标签
        last_end = match.end()
    plain_parts.append(text[last_end:])
    tag_parts.append(None)
    return plain_parts, tag_parts


def _rejoin_tags(converted_plain: str, tag_parts: list[str | None]) -> str:
    """将转换后的纯文本按标签结构重新拼接。tag_parts 中 None → 取 converted_plain 的下一个片段。"""
    # converted_plain 可能包含 \0 分隔的多段（对应多个纯文本片段）
    segments = converted_plain.split('\0')
    seg_idx = 0
    result: list[str] = []
    for part in tag_parts:
        if part is None:
            result.append(segments[seg_idx] if seg_idx < len(segments) else '')
            seg_idx += 1
        else:
            result.append(part)
    return ''.join(result)


# ── ASS 字幕 ──────────────────────────────────────────

def convert_ass_file(input_path, output_folder, direction, converter,
                     log_callback=None, is_cancelled_callback=None,
                     force_encoding=None):
    """将 ASS/SSA 字幕文件进行繁简转换（批量模式）"""
    def log(msg):
        if log_callback:
            log_callback(msg)

    if is_cancelled_callback and is_cancelled_callback():
        return False

    try:
        if not os.path.exists(input_path):
            log(f"错误：文件不存在 - {input_path}")
            return False
        os.makedirs(output_folder, exist_ok=True)
        log(f"正在处理ASS/SSA字幕文件: {os.path.basename(input_path)}")

        encoding = detect_encoding(input_path, log_callback, force_encoding)
        content = safe_read_file(input_path, encoding, log_callback)

        if is_cancelled_callback and is_cancelled_callback():
            return False

        lines = content.split('\n')
        in_events = False
        text_indices: list[int] = []
        text_entries: list[str] = []       # 纯文本片段（\0 分隔）
        line_templates: list[list[str | None]] = []  # 每行的标签结构

        for i, line in enumerate(lines):
            stripped = line.strip().lower()
            if stripped == '[events]':
                in_events = True
                continue
            if stripped.startswith('[') and stripped.endswith(']'):
                in_events = False
                continue

            if in_events and (stripped.startswith('dialogue:') or stripped.startswith('comment:')):
                # 提取 Text 字段
                if stripped.startswith('dialogue:'):
                    prefix = line[:9]
                    rest = line[9:]
                else:
                    prefix = line[:8]
                    rest = line[8:]
                parts = rest.split(',', 9)
                if len(parts) >= 10:
                    plain_parts, tag_parts = _split_tags(parts[9])
                    text_entries.append('\0'.join(plain_parts))
                    text_indices.append(i)
                    line_templates.append(tag_parts)

        if text_entries:
            big_text = '\1'.join(text_entries)
            converted_big = converter.convert(big_text, direction)
            converted_entries = converted_big.split('\1')

            for j, line_idx in enumerate(text_indices):
                parts = lines[line_idx].split(',', 9)
                parts[9] = _rejoin_tags(converted_entries[j], line_templates[j])
                if lines[line_idx].lower().startswith('dialogue:'):
                    lines[line_idx] = 'Dialogue:' + ','.join(parts)
                else:
                    lines[line_idx] = 'Comment:' + ','.join(parts)

        converted_content = '\n'.join(lines)
        output_filename = f"convert_{os.path.basename(input_path)}"
        output_path = os.path.join(output_folder, output_filename)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(converted_content)
        log(f"已保存: {output_path}")
        return output_path
    except Exception as e:
        log(f"处理ASS/SSA字幕文件 {input_path} 时出错: {str(e)}")
        return False


# ── LRC 歌词 ──────────────────────────────────────────

_LRC_TAG_RE = re.compile(r'<\d+>')


def convert_lrc_file(input_path, output_folder, direction, converter,
                     log_callback=None, is_cancelled_callback=None,
                     force_encoding=None):
    """将 LRC 歌词文件进行繁简转换（批量模式）"""
    def log(msg):
        if log_callback:
            log_callback(msg)

    if is_cancelled_callback and is_cancelled_callback():
        return False

    try:
        if not os.path.exists(input_path):
            log(f"错误：文件不存在 - {input_path}")
            return False
        os.makedirs(output_folder, exist_ok=True)
        log(f"正在处理LRC歌词文件: {os.path.basename(input_path)}")

        encoding = detect_encoding(input_path, log_callback, force_encoding)
        content = safe_read_file(input_path, encoding, log_callback)

        if is_cancelled_callback and is_cancelled_callback():
            return False

        lines = content.split('\n')
        id_tags = {'ti', 'ar', 'al', 'by', 're', 've', 'offset'}

        # 收集所有需要转换的 ID 标签内容和歌词文本
        all_plain: list[str] = []           # 所有纯文本
        id_indices: list[int] = []           # ID 标签行索引
        lyric_indices: list[tuple[int, list[str | None]]] = []  # (行索引, tag_structure)

        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue

            id_match = re.match(r'^\[([a-zA-Z]+):(.+)\]$', stripped)
            if id_match:
                tag_name = id_match.group(1).lower()
                if tag_name in id_tags:
                    all_plain.append(id_match.group(2))
                    id_indices.append(i)
                continue

            time_match = re.match(r'^((?:\[\d+:\d+(?:\.\d+)?\])+)(.*)$', stripped)
            if time_match:
                lyric_text = time_match.group(2)
                if lyric_text:
                    plain_parts, tag_parts = _split_lrc(lyric_text)
                    all_plain.append('\0'.join(plain_parts))
                    lyric_indices.append((i, tag_parts))

        if all_plain:
            big_text = '\1'.join(all_plain)
            converted_big = converter.convert(big_text, direction)
            converted_parts = converted_big.split('\1')

            # ID 标签
            for j, idx in enumerate(id_indices):
                stripped = lines[idx].strip()
                id_match = re.match(r'^\[([a-zA-Z]+):(.+)\]$', stripped)
                if id_match:
                    converted_line = f'[{id_match.group(1)}:{converted_parts[j]}]'
                    indent = lines[idx][:lines[idx].index('[')]
                    lines[idx] = indent + converted_line

            # 歌词行
            flat_start = len(id_indices)
            for j, (line_idx, tag_parts) in enumerate(lyric_indices):
                stripped = lines[line_idx].strip()
                time_match = re.match(r'^((?:\[\d+:\d+(?:\.\d+)?\])+)(.*)$', stripped)
                if time_match:
                    time_tags = time_match.group(1)
                    converted_lyric = _rejoin_lrc(converted_parts[flat_start + j], tag_parts)
                    indent = lines[line_idx][:lines[line_idx].index('[')] if '[' in lines[line_idx] else ''
                    lines[line_idx] = indent + time_tags + converted_lyric

        converted_content = '\n'.join(lines)
        output_filename = f"convert_{os.path.basename(input_path)}"
        output_path = os.path.join(output_folder, output_filename)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(converted_content)
        log(f"已保存: {output_path}")
        return output_path
    except Exception as e:
        log(f"处理LRC歌词文件 {input_path} 时出错: {str(e)}")
        return False


def _split_lrc(text: str) -> tuple[list[str], list[str | None]]:
    """拆分 LRC 歌词文本，保留 <nn> 标签"""
    plain_parts: list[str] = []
    tag_parts: list[str | None] = []
    last_end = 0
    for match in _LRC_TAG_RE.finditer(text):
        plain_parts.append(text[last_end:match.start()])
        tag_parts.append(None)
        tag_parts.append(match.group())
        last_end = match.end()
    plain_parts.append(text[last_end:])
    tag_parts.append(None)
    return plain_parts, tag_parts


def _rejoin_lrc(converted_plain: str, tag_parts: list[str | None]) -> str:
    """将转换后的 LRC 纯文本按标签结构重新拼接"""
    segments = converted_plain.split('\0')
    seg_idx = 0
    result: list[str] = []
    for part in tag_parts:
        if part is None:
            result.append(segments[seg_idx] if seg_idx < len(segments) else '')
            seg_idx += 1
        else:
            result.append(part)
    return ''.join(result)


# ── TXT 文本 ──────────────────────────────────────────

def convert_txt_file(input_path, output_folder, direction, converter,
                     log_callback=None, is_cancelled_callback=None,
                     force_encoding=None):
    """将 txt 文件进行繁简转换（使用 ts_converter AI 转换）"""
    def log(msg):
        if log_callback:
            log_callback(msg)

    if is_cancelled_callback and is_cancelled_callback():
        return False

    try:
        if not os.path.exists(input_path):
            log(f"错误：文件不存在 - {input_path}")
            return False
        os.makedirs(output_folder, exist_ok=True)
        log(f"正在处理txt文件: {os.path.basename(input_path)}")

        if is_cancelled_callback and is_cancelled_callback():
            return False

        encoding = detect_encoding(input_path, log_callback, force_encoding)
        log(f"最终使用的编码: {encoding}")
        content = safe_read_file(input_path, encoding, log_callback)

        if is_cancelled_callback and is_cancelled_callback():
            return False

        converted_content = converter.convert(content, direction)

        if is_cancelled_callback and is_cancelled_callback():
            return False

        output_filename = f"convert_{os.path.basename(input_path)}"
        output_path = os.path.join(output_folder, output_filename)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(converted_content)
        log(f"已保存: {output_path}")
        return output_path
    except Exception as e:
        log(f"处理txt文件 {input_path} 时出错: {str(e)}")
        return False
