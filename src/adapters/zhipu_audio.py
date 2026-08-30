"""智谱 BigModel 语音模型官方价（Tier 1 官方，人民币）。

定价页 open.bigmodel.cn/pricing 是 SPA，直接抓 HTML 拿不到数字——但它
背后有**公开的 JSON 接口**（无需登录、无需无头浏览器）::

    GET https://open.bigmodel.cn/api/biz/operation/query?ids=1122

``data[].content`` 是一段 JSON 字符串，里面 ``list[]`` 按区块分组
（语言模型 / 视觉理解 / 语音模型 …）。``modelList[]`` 的字段码是**混淆**的
（``idRXbS3`` / ``6MVccm`` 之类，每个区块还不一样），但同层的 ``fieldList``
给出了 code -> 中文标签的映射，所以能稳定按「模型」「单价」取值，
不依赖任何硬编码的字段码。

一家覆盖四种计价单位，这也是当初拆五张表的直接理由之一：

    GLM-TTS             2 元/万字符                  -> per_1m_chars
    GLM-TTS-Clone       6 元/次                      -> per_call
    GLM-ASR-2512        16 元/百万 tokens（输出不计费）-> input_per_1m
    GLM-4-Voice         80 元/百万 Tokens            -> input_per_1m
    GLM-Realtime-Flash  音频 0.18 元/分钟（视频 1.2） -> per_minute
    GLM-Realtime-Air    音频 0.3  元/分钟（视频 2.1） -> per_minute

⚠️ 实时音视频那两条**同时印了音频价和视频价**，只能取音频那一段——
   取错就会把视频价当成音频价（1.2 vs 0.18，差 6.7 倍）。

⚠️ token 价**故意不设 modality**：导出层的音频组按 modality=="audio" 分组，
   而按 token 的音频表走的是通用表结构（读 text_official_* 列）。标成
   audio 会让它从 token 表里消失——与 parse_litellm 的同一处考量一致。

⚠️ per_minute 的 **billing_basis 留空**：页面只写「音频：0.18元/分钟」，
   没说计的是输入音频、产出音频还是连接时长。实时接口通常按连接计费，
   但页面没写，就不替它断言——表里显示「未标注」是诚实状态。
   等官方文档明确后再补。

金额是**人民币**，交给 convert_records_to_usd 走 ECB 汇率统一换算（⇄ 标记）。
"""

from __future__ import annotations

import json
import re

from ..records import PriceRecord, TIER_OFFICIAL
from ..units import to_per_minute

ZHIPU_API = "https://open.bigmodel.cn/api/biz/operation/query?ids=1122"
ZHIPU_WEBLINK = "https://open.bigmodel.cn/pricing"

# 只收人工核对过、且 raw.csv 确有对应行的型号。
_SUPPORTED = {
    "GLM-TTS", "GLM-TTS-Clone", "GLM-ASR-2512",
    "GLM-4-Voice", "GLM-Realtime-Flash", "GLM-Realtime-Air",
}

_NUM = r"([0-9]+(?:\.[0-9]+)?)"
# 顺序即优先级：先匹配更具体的写法（「音频：X元/分钟」必须先于泛化的元/分钟）
_PATTERNS = (
    (re.compile(r"音频[：:\s]*" + _NUM + r"\s*元\s*/\s*分钟"), "audio_minute"),
    (re.compile(_NUM + r"\s*元\s*/\s*万字符"), "per_wan_chars"),
    (re.compile(_NUM + r"\s*元\s*/\s*次"), "per_call"),
    (re.compile(r"输入[：:\s]*" + _NUM + r"\s*元\s*/\s*百万\s*tokens", re.I), "input_1m"),
    (re.compile(_NUM + r"\s*元\s*/\s*百万\s*tokens", re.I), "input_1m"),
)


def _blocks(payload: dict):
    """产出 (区块名, {code: label}, [行])。"""
    for entry in payload.get("data") or []:
        raw = entry.get("content")
        if not isinstance(raw, str):
            continue
        try:
            content = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for group in content.get("list") or []:
            fields = {f.get("code"): f.get("label")
                      for f in group.get("fieldList") or []}
            yield group.get("modelName", ""), fields, group.get("modelList") or []


def parse_zhipu_audio(
    text: str, *, source_url: str, fetched_at: str, source_version: str | None
) -> tuple[list[PriceRecord], list[str]]:
    warnings: list[str] = []
    records: list[PriceRecord] = []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return [], [f"zhipu_audio: JSON 解析失败 {exc}"]

    seen: set[str] = set()
    for block_name, fields, rows in _blocks(payload):
        if "语音" not in block_name:
            continue
        # 按 fieldList 找出「模型」和「单价」两列的实际字段码。
        # 字段码是混淆的且逐区块不同，硬编码必然在下次改版时静默失效。
        col_model = next((c for c, label in fields.items() if label == "模型"), None)
        col_price = next((c for c, label in fields.items() if label == "单价"), None)
        if not col_model or not col_price:
            warnings.append(
                f"zhipu_audio: 区块「{block_name}」缺「模型」或「单价」列"
                f"（现有列：{list(fields.values())}）")
            continue

        for row in rows:
            model_id = str(row.get(col_model) or "").strip()
            price_text = str(row.get(col_price) or "").strip()
            if not model_id or not price_text:
                continue
            if model_id not in _SUPPORTED:
                warnings.append(
                    f"zhipu_audio: 未核验的新型号 {model_id!r}（{price_text}），已跳过。"
                    "确认后加入 _SUPPORTED。")
                continue
            if model_id in seen:
                continue

            kind = value = None
            for pattern, name in _PATTERNS:
                m = pattern.search(price_text)
                if m:
                    kind, value = name, float(m.group(1))
                    break
            if kind is None:
                warnings.append(
                    f"zhipu_audio: {model_id} 的单价写法无法识别：{price_text!r}")
                continue

            prices: dict = {}
            extra_raw: dict = {}
            basis = None
            if kind == "per_wan_chars":
                prices["per_1m_chars"] = value * 100        # 万 -> 百万
                unit = "CNY per 10k characters"
            elif kind == "per_call":
                prices["per_call"] = value
                unit = "CNY per call"
            elif kind == "input_1m":
                prices["input_per_1m"] = value
                unit = "CNY per 1M tokens"
            else:                                            # audio_minute
                prices["per_minute"], extra_raw = to_per_minute(value, "minute")
                unit = "CNY per minute of audio"

            seen.add(model_id)
            records.append(PriceRecord(
                source="zhipu_audio_official",
                source_tier=TIER_OFFICIAL,
                is_official=True,
                source_url=ZHIPU_WEBLINK,
                fetched_at=fetched_at,
                source_snippet=f"{model_id} | {price_text}",
                unit_original=unit,
                model_id=model_id,
                provider="Zhipu BigModel",
                currency="CNY",
                billing_basis=basis,
                source_version=source_version,
                raw={
                    "company": "Zhipu AI / GLM",
                    "provider_name": "Zhipu BigModel",
                    "weblink": ZHIPU_WEBLINK,
                    "seller_url": ZHIPU_WEBLINK,
                    **extra_raw,
                },
                **prices,
            ))

    if not records:
        warnings.append(
            "zhipu_audio: 一条语音价都没解析到，疑似接口 id 变了或区块改名。")
    return records, warnings
