"""模型名 → 网页上展示用的可读名。

`raw.csv` 的 `Model` 列是**规范标识符**：要么是可直接调用的 API id
（`gpt-5-mini`），要么是可直接拉取的 HF 仓库路径（`google/gemma-2-27b-it`）。
大小写在这两种场景下都是语义的一部分（HF 路径大小写敏感，写错 404），
所以 `Model` 永远不改。

这里只**另外派生**一个展示名，供网页做两行式渲染：

    Gemini 3.7 Flash          ← display_name（本模块产出）
    google/gemini-3.7-flash   ← Model（原样，等宽字体）

派生是有损的（`gpt-4o` → `GPT-4o`），所以只用于展示，任何匹配、去重、
调用都必须继续用 `Model`。

⚠️ **display_name 不唯一**，当前有 25 组重名。这不是 bug：raw.csv 里同一个
模型会分别以 API 形态和权重形态各列一行（`MiniMax-M2` 与
`MiniMaxAI/MiniMax-M2`），两者的展示名本就相同，但可用性、价格来源都不同。
所以网页上**不能拿 display_name 当 key 或做去重**，那是 `Model` 的职责。
需要在 UI 上区分时用 `weights` 列（free/proprietary）或 `On/Off Line`。
"""

from __future__ import annotations

import re

# 保持固有大小写的词。派生规则是"词首大写"，但这些词有约定俗成的写法，
# 机械大写会产出 `Gpt` / `Ai` / `Vl` 这种明显外行的结果。
_ACRONYMS = {
    "gpt": "GPT", "vl": "VL", "ai": "AI", "api": "API", "llm": "LLM",
    "moe": "MoE", "it": "IT", "tts": "TTS", "asr": "ASR", "ocr": "OCR",
    "rag": "RAG", "sql": "SQL", "vs": "vs", "hd": "HD", "sd": "SD",
    "xl": "XL", "ui": "UI", "os": "OS", "mot": "MoT", "r1": "R1",
    "v1": "V1", "v2": "V2", "v3": "V3", "glm": "GLM", "vlm": "VLM",
    "mm": "MM", "kv": "KV", "e4b": "E4B", "e2b": "E2B",
    "oss": "OSS", "vlm": "VLM", "omni": "Omni", "sota": "SOTA",
    "bf16": "BF16", "fp8": "FP8",
}

# 厂商官方写法里保留连字符的系列名。`gpt-5` 官方就写 GPT-5 而不是 GPT 5，
# 机械按分隔符拆开会把品牌名拆散。键是首词（小写），值是官方连接符。
_HYPHENATED_FAMILIES = {
    "gpt": "-", "claude": " ", "gemini": " ", "llama": " ", "qwen": "",
    "glm": "-", "deepseek": "-", "kimi": " ", "phi": "-", "grok": "-",
    "ernie": "-", "hunyuan": "-", "minimax": "-", "command": " ",
    "granite": " ", "jamba": " ", "falcon": " ", "solar": " ",
    "nemotron": " ", "mimo": "-", "step": "-", "doubao": "-",
    "muse": " ",
    "motif": " ", "celeris": "-",
}

# `A4B` / `A3B` 这类 MoE 激活参数标记
_ACTIVE_PARAM = re.compile(r"^a(\d+(?:\.\d+)?)([bmk])$", re.I)

# HF 仓库常见的后缀，展示时保留但规范大小写
_SUFFIX_FORMS = {
    "instruct": "Instruct", "chat": "Chat", "base": "Base",
    "thinking": "Thinking", "reasoner": "Reasoner", "preview": "Preview",
    "latest": "Latest", "mini": "Mini", "nano": "Nano", "pro": "Pro",
    "flash": "Flash", "lite": "Lite", "turbo": "Turbo", "plus": "Plus",
    "max": "Max", "air": "Air", "ultra": "Ultra", "small": "Small",
    "medium": "Medium", "large": "Large", "coder": "Coder", "code": "Code",
    "vision": "Vision", "audio": "Audio", "image": "Image", "video": "Video",
    "embedding": "Embedding", "rerank": "Rerank", "guard": "Guard",
    "distill": "Distill", "instant": "Instant", "sonar": "Sonar",
}

# `27b` / `0.6b` / `270m` / `8x7b` 这类规格标记，统一成大写单位
_SIZE = re.compile(r"^(\d+(?:\.\d+)?)(x\d+(?:\.\d+)?)?([bmk])$", re.I)
# 纯版本/日期段，原样保留：`4.5` `2024-08-06` `20251101`
_NUMERIC = re.compile(r"^[\d.]+$")


def _word(token: str) -> str:
    """规范化单个词的大小写。"""
    low = token.lower()

    if low in _ACRONYMS:
        return _ACRONYMS[low]
    if low in _SUFFIX_FORMS:
        return _SUFFIX_FORMS[low]
    if _NUMERIC.match(token):
        return token

    if match := _SIZE.match(token):
        number, mult, unit = match.groups()
        return f"{number}{mult or ''}{unit.upper()}"

    # MoE 激活参数：`a4b` → `A4B`
    if match := _ACTIVE_PARAM.match(token):
        number, unit = match.groups()
        return f"A{number}{unit.upper()}"

    # 形如 `gemma-3n` / `qwen3` 的字母数字混合：字母首字母大写，数字原样
    if any(c.isdigit() for c in low) and any(c.isalpha() for c in low):
        return low[0].upper() + low[1:]

    return token[0].upper() + token[1:] if token else token


# 派生规则做不出来的固有写法，直接覆盖。只放**厂商官方明确如此写**的，
# 不放个人偏好——这张表越小越好，它的每一条都是派生规则的一个缺口。
_OVERRIDES = {
    "motif-2-12.7b": "Motif 2 12.7B",
    "motif-2.6b": "Motif 2.6B",
    "nex-n2-mini": "Nex-N2-Mini",
    "longcat": "LongCat",
    "heavymode": "HeavyMode",
    "zigzag": "ZigZag",
    "internlm3": "InternLM3",
    "gpt-oss": "GPT-OSS",
    "qwen3.8": "Qwen3.8",
    "2.4t": "2.4T",
    "longcat-2.0": "LongCat-2.0",
    "nex-n2-pro": "Nex-N2-Pro",
    "minimax": "MiniMax",
    "sensenova": "SenseNova",
    "hy-vision-2.0-instruct": "Hunyuan Vision 2.0 Instruct",
    "hunyuan-t1-vision-20250916": "Hunyuan Vision 1.5 Thinking",
    "hunyuan-turbos-vision-video-20250728": "Hunyuan Vision Video",
    "hyperclovax": "HyperCLOVAX",
    "hyperclova": "HyperCLOVA",
    "internvl": "InternVL",
    "cogvideox": "CogVideoX",
    "cogview": "CogView",
    "seedream": "Seedream",
    "seedance": "Seedance",
    "mistral": "Mistral",
    "codestral": "Codestral",
    "devstral": "Devstral",
    "magistral": "Magistral",
    "pixtral": "Pixtral",
    "voxtral": "Voxtral",
    "ministral": "Ministral",
}


def _mask_overrides(text: str) -> tuple[str, dict[str, str]]:
    """把固有写法替换成不含分隔符的占位符，切词后再还原。

    直接替换会被后续按 `-` 切词的步骤拆散（`GPT-OSS` → `GPT` + `OSS`），
    所以先屏蔽成原子单位。长键优先，`hyperclovax` 先于 `hyperclova`。
    """
    masks: dict[str, str] = {}
    for index, key in enumerate(sorted(_OVERRIDES, key=len, reverse=True)):
        token = f"ZQMASK{index}ZQ"
        new_text = re.sub(
            rf"(?<![A-Za-z0-9]){re.escape(key)}(?![A-Za-z0-9])",
            token,
            text,
            flags=re.I,
        )
        if new_text != text:
            masks[token] = _OVERRIDES[key]
            text = new_text
    return text, masks


def display_name(model: str, company: str | None = None) -> str:
    """`google/gemma-2-27b-it` → `Gemma 2 27B IT`

    已经是可读名的（含空格且首字母大写）原样返回——人工写的名字
    总比机械派生的准确。
    """
    raw = (model or "").strip()
    if not raw:
        return ""

    # HF 路径式：org 段是组织名不是模型名，去掉
    # （`google/gemma-2-27b-it` 的展示名是 Gemma 而不是 Google Gemma）
    if "/" in raw and " / " not in raw:
        raw = raw.split("/")[-1]

    # 固有写法在切词前先屏蔽，避免 `gpt-oss` 被拆成两个词后接不回来
    raw, masks = _mask_overrides(raw)

    # 已含空格且首字母大写 = 人工写的可读名，不再加工。
    # `GPT-5.5 Instant` / `SenseNova V6 Pro` 属此类，机械派生只会更差。
    if " " in raw and raw[0].isupper():
        return _unmask(raw, masks)

    tokens = [t for t in re.split(r"[-_.\s]+", raw) if t]
    if not tokens:
        return raw

    # 小数版本号会被上面的 `.` 切开，先合回来。两种形态都要还原：
    #   `gpt-5.1`      → [gpt, 5, 1]   前一段是纯数字
    #   `MiniMax-M2.7` → [MiniMax, M2, 7]  前一段是 `M2` 这类字母数字混合
    # 只在前一段确实以数字结尾时合并，避免把 `Llama-3` + `70b` 接成 `3.70b`。
    merged: list[str] = []
    for token in tokens:
        if (
            merged
            and _NUMERIC.match(token)
            and "." not in merged[-1]
            and merged[-1][-1].isdigit()
        ):
            merged[-1] = f"{merged[-1]}.{token}"
        else:
            merged.append(token)

    words = [_word(t) for t in merged]

    # 系列名与紧随其后的版本号按厂商官方写法连接：
    # `gpt-5-mini` → `GPT-5 Mini`（不是 `GPT 5 Mini`），`qwen3-32b` → `Qwen3 32B`
    joiner = _HYPHENATED_FAMILIES.get(merged[0].lower())
    if joiner is not None and len(words) >= 2 and _NUMERIC.match(merged[1]):
        head = f"{words[0]}{joiner}{words[1]}"
        words = [head] + words[2:]

    return _unmask(" ".join(words), masks)


def _unmask(text: str, masks: dict[str, str]) -> str:
    for token, value in masks.items():
        text = re.sub(token, value, text, flags=re.I)
    return text
