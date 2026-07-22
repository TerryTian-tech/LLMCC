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

def convert_srt_file(input_path, output_folder, direction, converter,
                     log_callback=None, is_cancelled_callback=None,
                     force_encoding=None):
    """将 SRT 字幕文件进行繁简转换"""
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

        if is_cancelled_callback and is_cancelled_callback():
            return False

        encoding = detect_encoding(input_path, log_callback, force_encoding)
        log(f"最终使用的编码: {encoding}")
        content = safe_read_file(input_path, encoding, log_callback)

        if is_cancelled_callback and is_cancelled_callback():
            return False

        lines = content.split('\n')
        converted_lines = []
        i = 0

        while i < len(lines):
            if is_cancelled_callback and is_cancelled_callback():
                return False

            line = lines[i]
            if line.strip().isdigit() and i+1 < len(lines) and '-->' in lines[i+1]:
                converted_lines.append(line)
                i += 1
                if i < len(lines):
                    time_line = lines[i]
                    if '-->' in time_line:
                        converted_lines.append(time_line)
                        i += 1
                        while i < len(lines):
                            text_line = lines[i]
                            if text_line.strip() == '':
                                converted_lines.append(text_line)
                                i += 1
                                break
                            if text_line.strip().isdigit():
                                break
                            converted_text = _convert_text_with_tags(
                                converter, direction, text_line)
                            converted_lines.append(converted_text)
                            i += 1
                    else:
                        converted_lines.append(line)
                        i += 1
                else:
                    i += 1
            else:
                converted_lines.append(line)
                i += 1

        if is_cancelled_callback and is_cancelled_callback():
            return False

        converted_content = '\n'.join(converted_lines)
        output_filename = f"convert_{os.path.basename(input_path)}"
        output_path = os.path.join(output_folder, output_filename)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(converted_content)
        log(f"已保存: {output_path}")
        return output_path
    except Exception as e:
        log(f"处理SRT字幕文件 {input_path} 时出错: {str(e)}")
        return False


def _convert_text_with_tags(converter, direction, text):
    """转换字幕文本，保留 ASS/SSA 样式标签 {\\...}"""
    result = []
    last_end = 0
    pattern = re.compile(r'\{[^}]*\}')
    for match in pattern.finditer(text):
        plain_text = text[last_end:match.start()]
        if plain_text:
            result.append(converter.convert(plain_text, direction))
        result.append(match.group())
        last_end = match.end()
    remaining_text = text[last_end:]
    if remaining_text:
        result.append(converter.convert(remaining_text, direction))
    return ''.join(result)


# ── ASS 字幕 ──────────────────────────────────────────

def convert_ass_file(input_path, output_folder, direction, converter,
                     log_callback=None, is_cancelled_callback=None,
                     force_encoding=None):
    """将 ASS/SSA 字幕文件进行繁简转换"""
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

        if is_cancelled_callback and is_cancelled_callback():
            return False

        encoding = detect_encoding(input_path, log_callback, force_encoding)
        log(f"最终使用的编码: {encoding}")
        content = safe_read_file(input_path, encoding, log_callback)

        if is_cancelled_callback and is_cancelled_callback():
            return False

        lines = content.split('\n')
        converted_lines = []
        in_events_section = False

        for line in lines:
            if is_cancelled_callback and is_cancelled_callback():
                return False

            stripped_line = line.strip()
            if stripped_line.lower() == '[events]':
                in_events_section = True
                converted_lines.append(line)
                continue
            if stripped_line.startswith('[') and stripped_line.endswith(']'):
                in_events_section = False
                converted_lines.append(line)
                continue

            if in_events_section and (stripped_line.lower().startswith('dialogue:') or
                                      stripped_line.lower().startswith('comment:')):
                converted_line = _convert_ass_line(converter, direction, line)
                converted_lines.append(converted_line)
            else:
                converted_lines.append(line)

        if is_cancelled_callback and is_cancelled_callback():
            return False

        converted_content = '\n'.join(converted_lines)
        output_filename = f"convert_{os.path.basename(input_path)}"
        output_path = os.path.join(output_folder, output_filename)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(converted_content)
        log(f"已保存: {output_path}")
        return output_path
    except Exception as e:
        log(f"处理ASS/SSA字幕文件 {input_path} 时出错: {str(e)}")
        return False


def _convert_ass_line(converter, direction, line):
    """转换 ASS dialogue 行，保留时间戳格式"""
    line_lower = line.lower()
    if line_lower.startswith('dialogue:'):
        prefix = line[:9]
        rest = line[9:]
    elif line_lower.startswith('comment:'):
        prefix = line[:8]
        rest = line[8:]
    else:
        return line

    parts = rest.split(',', 9)
    if len(parts) < 10:
        return line

    parts[9] = _convert_text_with_tags(converter, direction, parts[9])
    return prefix + ','.join(parts)


# ── LRC 歌词 ──────────────────────────────────────────

def convert_lrc_file(input_path, output_folder, direction, converter,
                     log_callback=None, is_cancelled_callback=None,
                     force_encoding=None):
    """将 LRC 歌词文件进行繁简转换"""
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

        if is_cancelled_callback and is_cancelled_callback():
            return False

        encoding = detect_encoding(input_path, log_callback, force_encoding)
        log(f"最终使用的编码: {encoding}")
        content = safe_read_file(input_path, encoding, log_callback)

        if is_cancelled_callback and is_cancelled_callback():
            return False

        lines = content.split('\n')
        converted_lines = []
        id_tags = ['ti', 'ar', 'al', 'by', 're', 've', 'offset']

        for line in lines:
            if is_cancelled_callback and is_cancelled_callback():
                return False

            stripped_line = line.strip()
            if not stripped_line:
                converted_lines.append(line)
                continue

            id_tag_match = re.match(r'^\[([a-zA-Z]+):(.+)\]$', stripped_line)
            if id_tag_match:
                tag_name = id_tag_match.group(1).lower()
                tag_content = id_tag_match.group(2)
                if tag_name in id_tags:
                    converted_content = converter.convert(tag_content, direction)
                    converted_line = f'[{id_tag_match.group(1)}:{converted_content}]'
                    indent = line[:line.index('[')]
                    converted_lines.append(indent + converted_line)
                else:
                    converted_lines.append(line)
                continue

            time_tag_pattern = r'^((?:\[\d+:\d+(?:\.\d+)?\])+)(.*)$'
            time_match = re.match(time_tag_pattern, stripped_line)
            if time_match:
                time_tags = time_match.group(1)
                lyric_text = time_match.group(2)
                converted_lyric = _convert_lrc_text(converter, direction, lyric_text)
                indent = line[:line.index('[')] if '[' in line else ''
                converted_lines.append(indent + time_tags + converted_lyric)
            else:
                converted_lines.append(line)

        if is_cancelled_callback and is_cancelled_callback():
            return False

        converted_content = '\n'.join(converted_lines)
        output_filename = f"convert_{os.path.basename(input_path)}"
        output_path = os.path.join(output_folder, output_filename)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(converted_content)
        log(f"已保存: {output_path}")
        return output_path
    except Exception as e:
        log(f"处理LRC歌词文件 {input_path} 时出错: {str(e)}")
        return False


def _convert_lrc_text(converter, direction, text):
    """转换LRC歌词文本，保留增强型时间标签 <数字>"""
    if not text:
        return text
    result = []
    last_end = 0
    pattern = re.compile(r'<\d+>')
    for match in pattern.finditer(text):
        plain_text = text[last_end:match.start()]
        if plain_text:
            result.append(converter.convert(plain_text, direction))
        result.append(match.group())
        last_end = match.end()
    remaining_text = text[last_end:]
    if remaining_text:
        result.append(converter.convert(remaining_text, direction))
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
