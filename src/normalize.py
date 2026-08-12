"""raw.csv 的字段规范化。

三件事：
1. `On/Off Line` 的 43 种自由文本 → 结构化的 access_mode / platform / lifecycle
2. `Total Para` / `Activate Para` → 数值（单位 B）
3. `Model` 名字 → 一组用于匹配的候选形式

第 3 件是匹配层的地基：raw.csv 的模型名混了 HF 路径式、API id 式、
人类可读名三种风格，规范化的目标是让这三种都能落到同一个可比形式上。
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

# ── On/Off Line ────────────────────────────────────────────────

ACCESS_ONLINE = "online"
ACCESS_OFFLINE = "offline"
ACCESS_BOTH = "both"

# 括号里的生命周期标注。键按小写前缀匹配，因为实际取值带尾巴，
# 例如 "Deprecated, still available" 和 "Legacy; EOL 2026-09-30"。
_LIFECYCLE_PREFIXES = (
    ("deprecated", "deprecated"),
    ("legacy", "legacy"),
    ("preview", "preview"),
    ("experimental", "experimental"),
    ("stable", "stable"),
    ("active", "stable"),
)

_PAREN = re.compile(r"\(([^)]*)\)")


@dataclass
class Availability:
    access_mode: str
    platform: str | None
    lifecycle: str
    is_open_weight: bool
    is_private: bool
    raw: str


def parse_availability(value: str) -> Availability:
    """`Online: Gemini API (Deprecated, still available)` →
    access_mode=online, platform='Gemini API', lifecycle='deprecated'
    """
    raw = (value or "").strip()
    text = raw

    lifecycle = "stable"
    if match := _PAREN.search(text):
        inner = match.group(1).strip().lower()
        for prefix, canonical in _LIFECYCLE_PREFIXES:
            if inner.startswith(prefix):
                lifecycle = canonical
                break
        text = (text[: match.start()] + text[match.end() :]).strip()

    # `Online / Open Weight` 没有冒号，但语义上就是 Both
    if ":" in text:
        prefix, _, rest = text.partition(":")
        prefix = prefix.strip().lower()
    else:
        prefix, rest = text.strip().lower(), ""

    if prefix.startswith("both"):
        access_mode = ACCESS_BOTH
    elif prefix.startswith("offline"):
        access_mode = ACCESS_OFFLINE
    elif prefix.startswith("online"):
        # "Online / Open Weight"：前缀里就带了 Open Weight
        access_mode = ACCESS_BOTH if "open weight" in prefix else ACCESS_ONLINE
    else:
        access_mode = ACCESS_ONLINE

    is_private = "private" in raw.lower()
    is_open_weight = access_mode in (ACCESS_OFFLINE, ACCESS_BOTH) or (
        "open weight" in raw.lower()
    )

    # platform 是剥掉分发方式描述后剩下的服务名
    parts = [p.strip() for p in rest.split("/") if p.strip()]
    noise = {
        "open weight",
        "official hugging face",
        "official hugging face open weight",
        "private",
    }
    platform_parts = [p for p in parts if p.lower() not in noise]
    platform = " / ".join(platform_parts) or None

    return Availability(
        access_mode=access_mode,
        platform=platform,
        lifecycle=lifecycle,
        is_open_weight=is_open_weight,
        is_private=is_private,
        raw=raw,
    )


# ── 参数量 ──────────────────────────────────────────────────────

_PARAM = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([BbMmKk])\b")


def parse_params(value: str) -> float | None:
    """`8B` → 8.0，`90M` → 0.09，`3B/token` → 3.0（剥掉 MoE 后缀）。

    统一以 B（十亿）为单位。`Undisclosed` 和空值返回 None。
    """
    text = (value or "").strip()
    if not text or text.lower() in ("undisclosed", "unknown", "n/a", "-"):
        return None
    match = _PARAM.match(text)
    if not match:
        return None
    number = float(match.group(1))
    suffix = match.group(2).lower()
    if suffix == "m":
        number /= 1_000
    elif suffix == "k":
        number /= 1_000_000
    return round(number, 6)


# ── 模型名规范化 ────────────────────────────────────────────────

# 尾部的版本括号不是名字的一部分：`Ministral 3 3B (v25.12)`
_VERSION_PAREN = re.compile(r"\s*\((?:v[\d.]+|[^)]*\bv[\d.]+)\)\s*$", re.I)
# 其余尾部括号（如 `(< 200k prompt tokens)`）在匹配时也应剥掉
_ANY_TRAILING_PAREN = re.compile(r"\s*\([^)]*\)\s*$")
_SEPARATORS = re.compile(r"[\s_.]+")
_MULTI_DASH = re.compile(r"-{2,}")
# 托管平台的版本尾巴：`amazon.nova-lite-v1:0` 在 Bedrock 上就叫 `Nova Lite`
_PLATFORM_VERSION_SUFFIX = re.compile(r"-v\d+(?::\d+)?$", re.I)

# 公司名到可能出现在模型名开头的前缀，用于剥掉冗余前缀
_COMPANY_PREFIXES = {
    "OpenAI": ["openai"],
    "Anthropic": ["anthropic", "claude"],
    "Google": ["google", "deepmind"],
    "Meta": ["meta", "meta-llama", "facebook"],
    "xAI": ["xai", "x-ai"],
    "Mistral AI": ["mistralai", "mistral"],
    "DeepSeek": ["deepseek-ai", "deepseek"],
    "MiniMax": ["minimaxai", "minimax"],
    "Microsoft": ["microsoft"],
    "Cohere": ["coherelabs", "cohereforai", "cohere"],
    "NVIDIA": ["nvidia"],
    "StepFun": ["stepfun-ai", "stepfun"],
    "IBM": ["ibm-granite", "ibm"],
    "AI21 Labs": ["ai21labs", "ai21"],
    "Upstage": ["upstage"],
    "NAVER": ["naver-hyperclovax", "naver"],
    "Baichuan AI": ["baichuan-inc", "baichuan"],
    "iFLYTEK": ["iflytekopensource", "iflytek"],
    "SenseTime": ["sensenova", "sensetime"],
    "TII / Falcon": ["tiiuae", "tii"],
    "Alibaba / Qwen": ["qwen", "alibaba"],
    "Amazon / Nova": ["amazon", "aws"],
    "Baidu / ERNIE": ["baidu"],
    "ByteDance / Doubao-Seed": ["bytedance-seed", "bytedance"],
    "Moonshot AI / Kimi": ["moonshotai", "moonshot"],
    "Tencent / Hunyuan": ["tencent"],
    "Xiaomi / MiMo": ["xiaomimimo", "xiaomi"],
    "Zhipu AI / GLM": ["zai-org", "zhipuai", "zhipu", "thudm"],
}


def norm(text: str) -> str:
    """把一个名字压到可比较的规范形式。"""
    value = (text or "").strip().lower()
    value = _SEPARATORS.sub("-", value)
    value = _MULTI_DASH.sub("-", value)
    return value.strip("-")


def name_candidates(model: str, company: str | None = None) -> list[str]:
    """一个 raw.csv 模型名 → 一组用于匹配的候选形式，粗到细排列。

    raw.csv 里有三种命名风格混用，还有 `Spark 4.0 Ultra / 4.0Ultra` 这种
    一格塞两个名字的写法（斜杠两边都是有效名字，必须都试）。
    """
    raw = (model or "").strip()
    if not raw:
        return []

    seeds: list[str] = [raw]

    # HF 路径式：org/repo —— 全名和 repo 部分都可能是匹配键
    if "/" in raw and " / " not in raw:
        seeds.append(raw.split("/")[-1])

    # ` / ` 分隔的并列名（注意与 HF 路径的 `/` 区分：这里两边有空格）
    if " / " in raw:
        seeds.extend(part.strip() for part in raw.split(" / "))

    expanded: list[str] = []
    for seed in seeds:
        expanded.append(seed)
        stripped_version = _VERSION_PAREN.sub("", seed).strip()
        if stripped_version != seed:
            expanded.append(stripped_version)
        stripped_paren = _ANY_TRAILING_PAREN.sub("", seed).strip()
        if stripped_paren != seed:
            expanded.append(stripped_paren)

    out: list[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        if value and value not in seen:
            seen.add(value)
            out.append(value)

    for item in expanded:
        normalized = norm(item)
        if not normalized:
            continue
        _add(normalized)

        # 剥掉与 Company 重复的前缀：`qwen-qwen3-embedding-0.6b` → `qwen3-embedding-0.6b`
        # ⚠️ 这一步必须**对源侧和 raw 侧同样施加**，否则
        # `NVIDIA Nemotron 3 Super` 和 `Nemotron 3 Super` 永远对不上。
        variants = [normalized]
        for prefix in _COMPANY_PREFIXES.get(company or "", []):
            if normalized.startswith(f"{prefix}-"):
                trimmed = normalized[len(prefix) + 1 :]
                if trimmed:
                    variants.append(trimmed)

        for variant in variants:
            _add(variant)
            # 托管平台常把版本尾巴去掉：`nova-lite-v1:0` 在 Bedrock 上是 `nova-lite`
            _add(_PLATFORM_VERSION_SUFFIX.sub("", variant))
            # 厂商官方文档写的是不带日期的名字（`Claude Opus 4.5`），而聚合器用
            # 带日期快照的 id（`claude-opus-4-5-20251101`）。不生成去日期候选，
            # 精确匹配就只会命中聚合器，官方牌价被挡住、出处也归错。
            _add(_strip_date_suffix(variant))

    return out


def _strip_date_suffix(value: str) -> str:
    """剥掉结尾的日期快照，保留版本号。

    `claude-opus-4-5-20251101` → `claude-opus-4-5`
    `gpt-4o-2024-08-06`        → `gpt-4o`
    `grok-4-20-0309`           → `grok-4-20`   （只剥一段，不能把 4-20 也当日期）
    `qwen3-32b`                → 原样           （32b 不是纯数字）
    """
    parts = value.split("-")
    # YYYY-MM-DD 形式：末三段位数为 4/2/2
    if len(parts) >= 4:
        tail = parts[-3:]
        if all(p.isdigit() for p in tail) and [len(p) for p in tail] == [4, 2, 2]:
            return "-".join(parts[:-3])
    # 单段日期：末段纯数字且 ≥4 位（20251101 / 0309）。
    # 限定 ≥4 位是为了不把版本号分量（M2 的 `2`、4.5 的 `5`）当成日期剥掉。
    if len(parts) >= 2 and parts[-1].isdigit() and len(parts[-1]) >= 4:
        return "-".join(parts[:-1])
    return value


# ── 从模型 id 推断公司 ──────────────────────────────────────────
# 匹配层按公司限定范围，是防止跨公司误配的结构性防线（iFLYTEK 的 Spark 与
# OpenAI 的 codex-spark 就是靠它隔开的）。聚合器/托管方给的 id 没有公司字段，
# 必须先归属才能进匹配。
#
# 顺序有意义：先匹配到的胜出，更具体的写在前面。

_COMPANY_MARKERS: tuple[tuple[str, str], ...] = (
    # HF org 前缀最可靠，优先
    ("qwen/", "Alibaba / Qwen"),
    ("deepseek-ai/", "DeepSeek"),
    ("meta-llama/", "Meta"),
    ("meta-models/", "Meta"),
    ("mistralai/", "Mistral AI"),
    ("moonshotai/", "Moonshot AI / Kimi"),
    ("zai-org/", "Zhipu AI / GLM"),
    ("thudm/", "Zhipu AI / GLM"),
    ("minimaxai/", "MiniMax"),
    ("bytedance-seed/", "ByteDance / Doubao-Seed"),
    ("bytedance/", "ByteDance / Doubao-Seed"),
    ("stepfun-ai/", "StepFun"),
    ("xiaomimimo/", "Xiaomi / MiMo"),
    ("ibm-granite/", "IBM"),
    ("ai21labs/", "AI21 Labs"),
    ("tiiuae/", "TII / Falcon"),
    ("baichuan-inc/", "Baichuan AI"),
    ("naver-hyperclovax/", "NAVER"),
    ("sensenova/", "SenseTime"),
    ("coherelabs/", "Cohere"),
    ("cohereforai/", "Cohere"),
    ("upstage/", "Upstage"),
    ("nvidia/", "NVIDIA"),
    ("google/", "Google"),
    ("openai/", "OpenAI"),
    ("anthropic/", "Anthropic"),
    ("x-ai/", "xAI"),
    ("xai/", "xAI"),
    ("microsoft/", "Microsoft"),
    ("amazon/", "Amazon / Nova"),
    ("alibaba/", "Alibaba / Qwen"),
    ("tencent/", "Tencent / Hunyuan"),
    ("baidu/", "Baidu / ERNIE"),
    ("z-ai/", "Zhipu AI / GLM"),
    ("zhipu", "Zhipu AI / GLM"),
    ("minimax", "MiniMax"),
    # 再按模型名关键词
    ("claude", "Anthropic"),
    ("gpt-oss", "OpenAI"),
    ("gpt-", "OpenAI"),
    ("gpt ", "OpenAI"),
    ("o1-", "OpenAI"),
    ("o3-", "OpenAI"),
    ("o4-", "OpenAI"),
    ("text-embedding-", "OpenAI"),
    ("whisper", "OpenAI"),
    ("dall-e", "OpenAI"),
    ("gemini", "Google"),
    ("gemma", "Google"),
    ("imagen", "Google"),
    ("veo-", "Google"),
    ("grok", "xAI"),
    ("llama", "Meta"),
    ("qwen", "Alibaba / Qwen"),
    ("deepseek", "DeepSeek"),
    ("kimi", "Moonshot AI / Kimi"),
    ("glm-", "Zhipu AI / GLM"),
    ("glm", "Zhipu AI / GLM"),
    ("cogview", "Zhipu AI / GLM"),
    ("cogvideo", "Zhipu AI / GLM"),
    ("mistral", "Mistral AI"),
    ("mixtral", "Mistral AI"),
    ("ministral", "Mistral AI"),
    ("pixtral", "Mistral AI"),
    ("magistral", "Mistral AI"),
    ("devstral", "Mistral AI"),
    ("codestral", "Mistral AI"),
    ("voxtral", "Mistral AI"),
    ("command", "Cohere"),
    ("nova-", "Amazon / Nova"),
    ("titan-", "Amazon / Nova"),
    ("nemotron", "NVIDIA"),
    ("phi-", "Microsoft"),
    ("granite", "IBM"),
    ("jamba", "AI21 Labs"),
    ("falcon", "TII / Falcon"),
    ("solar-", "Upstage"),
    ("hyperclova", "NAVER"),
    ("baichuan", "Baichuan AI"),
    ("hunyuan", "Tencent / Hunyuan"),
    ("ernie", "Baidu / ERNIE"),
    ("doubao", "ByteDance / Doubao-Seed"),
    ("seedance", "ByteDance / Doubao-Seed"),
    ("seedream", "ByteDance / Doubao-Seed"),
    ("step-", "StepFun"),
    ("mimo", "Xiaomi / MiMo"),
    ("sensenova", "SenseTime"),
    ("spark", None),  # ⚠️ 有意置空：`spark` 会误命中 OpenAI 的 codex-spark
)


def infer_company(model_id: str) -> str | None:
    """从聚合器给的模型 id 推断它属于哪家公司。

    推断不出就返回 None，该记录不进匹配——**宁可少匹配也不要归错公司**，
    归错公司等于把跨公司误配的防线拆了。
    """
    low = (model_id or "").strip().lower()
    if not low:
        return None
    for marker, company in _COMPANY_MARKERS:
        if marker in low:
            return company
    return None


# ── raw.csv 载入 ────────────────────────────────────────────────

RAW_COLUMNS = [
    "Model",
    "Company",
    "Function",
    "Total Para",
    "Activate Para",
    "On/Off Line",
    "Reasoning",
]


@dataclass
class RawModel:
    model: str
    company: str
    function: str
    total_params_b: float | None
    active_params_b: float | None
    availability: Availability
    reasoning: bool
    row_index: int

    @property
    def candidates(self) -> list[str]:
        return name_candidates(self.model, self.company)



def load_raw(path: str | Path) -> list[RawModel]:
    rows: list[RawModel] = []
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = set(RAW_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"raw.csv 缺少列：{sorted(missing)}")
        for index, row in enumerate(reader):
            rows.append(
                RawModel(
                    model=row["Model"].strip(),
                    company=row["Company"].strip(),
                    function=row["Function"].strip(),
                    total_params_b=parse_params(row["Total Para"]),
                    active_params_b=parse_params(row["Activate Para"]),
                    availability=parse_availability(row["On/Off Line"]),
                    reasoning=row["Reasoning"].strip().lower() == "yes",
                    row_index=index,
                )
            )
    return rows
