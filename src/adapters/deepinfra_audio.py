"""DeepInfra 音频模型托管价（Tier 3 第三方）。

为什么单独写适配器而不是配进 ``price_apis.yaml``：

* 同一个字段在不同 ``type`` 下是**不同量纲**。``cents_per_output_sec`` 在
  ``text-to-video`` 上是「产出视频秒」（已由通用适配器映射到 per_second），
  在 ``text-to-music`` 上却是「产出音频秒」。通用适配器的字段映射是全表统一的，
  表达不了这个条件分支。
* 音频价需要同时写入 ``billing_basis``（输入音频 / 产出音频），
  而 YAML 里没有这个概念。

⚠️ **绝对不要映射 ``cents_per_sec``。**
   实测该字段值恒为 0.05，且同时出现在：
       openai/whisper-base            (automatic-speech-recognition)
       CompVis/stable-diffusion-v1-4  (text-to-image)
       openai/clip-vit-base-patch32   (zero-shot-image-classification)
   一个图像分类器不可能有「每秒音频价」——这是**GPU 计算秒**的旧版计费，
   不是音频时长。当成音频价会给 whisper 系列产出 $0.03/分钟的假价，
   比真实价（$0.00045/分钟）高 66 倍，而 0.05 这个数字本身看不出异常。

单位实测锚点（README 要求 unit 必须实测，不能猜）：

    Voxtral-Small-24B-2507   cents_per_input_sec=0.005      → $0.00300/分钟
    Voxtral-Mini-3B-2507     cents_per_input_sec=0.00166667 → $0.00100/分钟

  DeepInfra 官方定价页原文分别是 "$0.00300" 和 "$0.00100"，
  并明确标注 "per minute of audio input" —— 两个锚点精确吻合，
  确认 ``cents_per_input_sec`` 是「美分 / 输入音频秒」。

  按字符价的落点也自洽：Kokoro-82M $0.62/1M 字符、inworld-tts-1.5-max
  $50/1M 字符，正好覆盖 OpenAI tts-1（$15/1M）与 ElevenLabs（$50–100/1M）
  这个区间；若单位错一个数量级，两端都会离谱。
"""

from __future__ import annotations

import json

from ..normalize import infer_company
from ..records import PriceRecord, TIER_THIRDPARTY
from ..units import to_per_minute

DEEPINFRA_URL = "https://api.deepinfra.com/models/list"
DEEPINFRA_WEBLINK = "https://deepinfra.com/models"
_MODEL_URL = "https://deepinfra.com/{model}"

# type -> (字段, 目标价格字段, 源刻度, 计量对象)
# 只列音频三类。text-to-video 归通用适配器管，这里碰都不碰，
# 免得同一条观测被两个源重复计入 quote_count。
_RULES = {
    "automatic-speech-recognition": [
        ("cents_per_input_sec", "per_minute", "second", "input_audio"),
    ],
    "text-to-speech": [
        ("cents_per_input_chars", "per_1m_chars", "character", None),
    ],
    "text-to-music": [
        ("cents_per_output_sec", "per_minute", "second", "output_audio"),
    ],
}

# 源刻度 -> 换成表刻度的系数（已先 ÷100 把美分变美元）
_SCALE = {"character": 1_000_000.0}


def parse_deepinfra_audio(
    text: str, *, source_url: str, fetched_at: str, source_version: str | None
) -> tuple[list[PriceRecord], list[str]]:
    warnings: list[str] = []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return [], [f"deepinfra_audio: JSON 解析失败 {exc}"]
    if not isinstance(payload, list):
        return [], [f"deepinfra_audio: 期望列表，得到 {type(payload).__name__}"]

    records: list[PriceRecord] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        if item.get("deprecated") or item.get("private"):
            continue
        rules = _RULES.get(str(item.get("type") or ""))
        if not rules:
            continue
        model_id = item.get("model_name")
        if not model_id:
            continue
        pricing = item.get("pricing") or {}
        if not isinstance(pricing, dict):
            continue
        # 归属不明的记录建了也匹配不上（match 按公司建索引），
        # 与 parse_litellm 一致：直接跳过并计入告警，不静默丢弃。
        company = infer_company(model_id)
        if not company:
            warnings.append(
                f"deepinfra_audio: {model_id} 无法归属公司，已跳过"
                "（需在 normalize.py 补 marker）")
            continue

        for src_field, target, scale, basis in rules:
            raw_value = pricing.get(src_field)
            if not isinstance(raw_value, (int, float)) or isinstance(raw_value, bool):
                continue
            if raw_value <= 0:
                continue
            usd = float(raw_value) / 100.0          # 美分 -> 美元
            patch: dict = {}
            if target == "per_minute":
                value, patch = to_per_minute(usd, scale)
            else:
                value = usd * _SCALE[scale]

            try:
                records.append(PriceRecord(
                    source="deepinfra_audio",
                    source_tier=TIER_THIRDPARTY,
                    is_official=False,
                    source_url=DEEPINFRA_WEBLINK,
                    fetched_at=fetched_at,
                    source_snippet=f"{model_id} | type={item.get('type')} | "
                                   f"{src_field}={raw_value}",
                    unit_original=f"cents per {scale} (DeepInfra)",
                    model_id=model_id,
                    provider="DeepInfra",
                    modality="audio",
                    billing_basis=basis,
                    source_version=source_version,
                    raw={
                        "deepinfra_type": item.get("type"),
                        "seller_url": _MODEL_URL.format(model=model_id),
                        # ⚠️ 键名必须是 "company"：match._index_by_company
                        #    只认这个键，写错会让整个源静默匹配为 0。
                        "company": company,
                        **patch,
                    },
                    **{target: value},
                ))
            except ValueError as exc:
                warnings.append(f"deepinfra_audio: {model_id} 建记录失败 {exc}")

    if not records:
        warnings.append("deepinfra_audio: 一条音频价都没解析到，疑似上游字段改名")
    return records, warnings
