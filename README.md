# LLM Chinese Convert AI繁简转换

目前 LLM 大模型已经具备进行繁简转换的能力，即使不借助外部知识库，模型权重依然储备了相关知识，能根据语义进行精准的转换。

但是把主流的 LLM 大模型接入繁简转换工具，依然存在两个问题：

- 把整段文本丢进去，模型容易出现幻觉，或者不遵循指令去修改文本内容
- 模型设置了繁琐的安全拒答策略，造成转换失败

本工具尝试采用两步解决：

1. 能一对一转换的字，均统一替换；
2. 对存在一对多歧义的字，再调用大模型 API 根据上下文判断。

目前支持 DOC/DOCX、TXT、EPUB 和字幕文件转换；API 使用兼容 OpenAI 的 API 格式。

> [!NOTE]
> 以《史记·夏本纪》为测试文本进行转换，24000 个字符，耗时约 7 分钟。使用的模型为 Deepseek-V4-flash（预览版模型），消耗费用 0.16 元人民币。建议不要转换超大文件和超长文本，耗时过长且需要承担更多的token费用。

## 安装使用

```bash
pip install -r requirements.txt
python main.py
```

## 配置

首次运行 GUI 时填写 API Base URL、模型名和 API Key。配置会保存在 `.ts_converter_config.json` 中。
AI 判断结果会缓存在 `.ts_converter_cache.json ` 中，默认为当前用户的 `users/你的用户名` 目录下。

## 映射字表

项目使用以下四个映射文件（存放于 `data/` 目录）：

- `STCharacters-1.txt`：简→繁一对一（3535 行）
- `STCharacters-2.txt`：简→繁一对多（117 行）
- `TSCharacters-1.txt`：繁→简一对一（4724 行）
- `TSCharacters-2.txt`：繁→简一对多（85 行）

映射字表基于 [OpenCC](https://github.com/BYVoid/OpenCC) 和 [OpenCC-Traditional Chinese to Traditional Chinese (The Chinese Government Standard)](https://github.com/TerryTian-tech/OpenCC-Traditional-Chinese-characters-according-to-Chinese-government-standards) 制作，遵循《通用规范汉字表》（2013）标准。
