"""Tier 2：可 vendor 到本地的 MIT 开源价格数据集。

models.dev 和 LiteLLM 都是 MIT 许可，数据可以合法地永久保存一份副本。
这是对"上游站点挂掉"最直接的对冲：**先读本地 vendor/ 副本，读不到才联网**。

需要说清楚的一点：这两个数据集**不是抓来的，它们本身就是根源**。
models.dev 的 google.ts 里写着 `cost: existing.cost`（价格取自本地手工 TOML），
并注明 Google 的 Models API 不提供价格；LiteLLM 的 JSON 由人和 AI bot 依据
厂商公告手工维护。所以这部分无法"从根源自己获取"——能做的是合法留存副本。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..normalize import infer_company
from ..records import TIER_VENDORED, PriceRecord
from ..units import to_per_minute
from ..seller_urls import catalog_url_for

VENDOR_DIR = Path(__file__).resolve().parent.parent.parent / "vendor"

MODELSDEV_URL = "https://models.dev/api.json"
MODELSDEV_WEBLINK = "https://models.dev"
LITELLM_URL = (
    "https://cdn.jsdelivr.net/gh/BerriAI/litellm@main/"
    "model_prices_and_context_window.json"
)
LITELLM_WEBLINK = "https://github.com/BerriAI/litellm"

# 与非美元价的 ⇄ 同一类标记：≈ 表示「此价由数据源换算得出，不是厂商牌价」
DERIVED_MARKER = "≈"

# 有些 LiteLLM 第一方记录的 key 是裸模型名，唯一的厂商字段只有 provider。
# 这里只列不会产生歧义的模型厂商自营 provider；云平台（bedrock/azure/oci）
# 不能映射成模型公司。
_LITELLM_PROVIDER_COMPANIES = {
    "cohere": "Cohere",
}

# ── 图像质量/尺寸档 ────────────────────────────────────────────
# LiteLLM 把图像模型的档位编码在 key 的前缀段里：
#   low/1024-x-1024/gpt-image-1      $0.011/张
#   medium/1024-x-1024/gpt-image-1   $0.042/张
#   high/1024-x-1024/gpt-image-1     $0.167/张
# 只取最后一段当 model_id 会把这三档压成一个，最低档看起来就是"这个模型的
# 按张价"（实测差 15 倍）。与上下文分档同理：**一档一行**，不合并。
_IMG_QUALITY = re.compile(r"^(low|medium|high|hd|standard)$", re.I)
_IMG_SIZE = re.compile(r"^\d+-x-\d+$|^max-x-max$", re.I)
_IMG_STEPS = re.compile(r"^(?:\d+|max)-steps$", re.I)


def _image_qualifier(key: str) -> str | None:
    """从 LiteLLM 的 key 前缀里提取质量/尺寸/步数档。

    非档位的前缀段（`azure`、`fal_ai` 这类服务方）一律忽略——它们是卖家，
    不是档位。完全没有档位信息就返回 None（那是该模型的基准价）。

    ⚠️ 缺省质量归一到 `standard`：LiteLLM 同时存在 `1024-x-1024/gpt-image-1.5`
    和 `standard/1024-x-1024/gpt-image-1.5`，两者价格都是 $0.009，是同一档的
    两种写法。不归一就会分裂成两行重复档位。
    """
    quality = size = steps = None
    for segment in key.split("/")[:-1]:
        if _IMG_QUALITY.match(segment):
            quality = segment.lower()
        elif _IMG_SIZE.match(segment):
            size = segment.lower()
        elif _IMG_STEPS.match(segment):
            steps = segment.lower()
    if size is None and steps is None:
        # 只有 `standard/dall-e-3` 这种没有尺寸的，不构成可比较的档位
        return None
    return " / ".join(filter(None, [quality or "standard", size, steps]))


def local_path(name: str) -> Path:
    return VENDOR_DIR / f"{name}.json"


def load_local(name: str) -> Any | None:
    path = local_path(name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_local(name: str, text: str) -> None:
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    local_path(name).write_text(text, encoding="utf-8")


# 带尺寸/画质前缀的按张价**不是厂商公布的牌价，是 LiteLLM 替它换算的**。
# 实测：gpt-image-1 的 low/1024-x-1024 = $0.011，恰好等于官方图像输出 token
# 价 $40/1M × OpenAI 公布的 272 token/张；9 个档位全部整除干净
# （272→0.011、1056→0.042、4160→0.167…）。gpt-image-1.5 同理（$10/1M ×
# 900/1300/3400/5000/13300/20000）。nova-canvas 换算的则是步数而非 token。
#
# 这类值有用，但读者必须看得出它是**换算值而不是牌价**——和非美元价的 ⇄
# 一个道理：换算了就明说换算了。这里只打标，怎么展示交给导出层。
#
# ⚠️ 只有带前缀的才打标。裸键的按张价（dall-e-3 $0.04、imagen-4.0 $0.04、
#    nova-canvas $0.06）是厂商真牌价，不能一起标上。
def _derived_note(key: str, qualifier: str, per_image: float) -> str:
    return (f"按张价由数据源换算得出：LiteLLM 把「{qualifier}」这一档的用量折算为 "
            f"${per_image:g}/张；厂商本身按 token（或步数）计费，并未公布该档位的"
            f"按张牌价。原始键 {key}")


def parse_modelsdev(
    payload: dict, *, source_url: str, fetched_at: str, source_version: str | None
) -> list[PriceRecord]:
    """{provider_id: {models: {model_id: {..., cost: {input, output, ...}}}}}

    cost 已经是「每 100 万 token 美元」，不需换算。
    """
    records: list[PriceRecord] = []
    for provider_id, provider in payload.items():
        if not isinstance(provider, dict):
            continue
        for model_id, model in (provider.get("models") or {}).items():
            cost = model.get("cost") or {}
            if not isinstance(cost, dict):
                continue
            prices = {
                "input_per_1m": cost.get("input"),
                "output_per_1m": cost.get("output"),
                "cache_read_per_1m": cost.get("cache_read"),
                "cache_write_per_1m": cost.get("cache_write"),
            }
            prices = {
                k: float(v)
                for k, v in prices.items()
                if isinstance(v, (int, float)) and v > 0
            }
            if not prices:
                continue

            company = infer_company(model_id) or infer_company(model.get("name") or "")
            if not company:
                continue

            # models.dev 的 key 是 `卖家/模型路径`，provider_id 段就是**实际报价方**。
            # 卖家正是模型厂商自己时（`google/gemini-2.5-pro`），这条就是厂商牌价，
            # 不是转售价。一律 is_official=False 会把约 25 个官方价误标成 hosted，
            # 而判据其实就在 key 里——丢掉它才是信息损失。
            # 推断不出卖家归属时保持 False：宁可把官方价谦称为 hosted，
            # 也不能把转售价谎报成牌价。
            seller_company = infer_company(provider_id)
            is_first_party = seller_company == company

            limit = model.get("limit") or {}
            records.append(
                PriceRecord(
                    source="modelsdev",
                    source_tier=TIER_VENDORED,
                    is_official=is_first_party,
                    source_url=source_url,
                    fetched_at=fetched_at,
                    source_snippet=f"{provider_id}/{model_id} cost={json.dumps(cost)}",
                    unit_original="per 1M tokens (USD)",
                    source_version=source_version,
                    model_id=model_id,
                    provider=f"modelsdev/{provider_id}",
                    context_length=limit.get("context"),
                    max_output=limit.get("output"),
                    raw={
                        "company": company,
                        "weblink": MODELSDEV_WEBLINK,
                        "provider_name": "models.dev",
                        "open_weights": model.get("open_weights"),
                        "seller": provider_id,
                        # 商家自己的模型目录；source_url 仍保留价格数据证据。
                        "seller_url": (
                            catalog_url_for(provider_id)
                            or provider.get("doc")
                            or provider.get("api")
                            or ""
                        ),
                    },
                    **prices,
                )
            )
    return records


def parse_litellm(
    payload: dict, *, source_url: str, fetched_at: str, source_version: str | None
) -> list[PriceRecord]:
    """{model_key: {input_cost_per_token, output_cost_per_token, ...}}

    单位是「每 token 美元」，要 ×1e6。
    key 按**服务方**命名（`bedrock/ap-northeast-1/qwen...`），取最后一段作模型 id。
    """
    records: list[PriceRecord] = []
    for key, entry in payload.items():
        if key == "sample_spec" or not isinstance(entry, dict):
            continue
        # LiteLLM 这条直连 Cohere 记录把 0.10/1M 错写成了 0.0001/token
        #（即 100/1M）。同一数据集里的 OCI 四个区域记录均为 0.10/1M。
        # 仅在异常量级仍存在时跳过；上游修正后会自动恢复使用。
        if (
            key == "embed-multilingual-light-v3.0"
            and (entry.get("input_cost_per_token") or 0) >= 0.00001
        ):
            continue
        prices = {
            "input_per_1m": entry.get("input_cost_per_token"),
            "output_per_1m": entry.get("output_cost_per_token"),
            "cache_read_per_1m": entry.get("cache_read_input_token_cost"),
            "cache_write_per_1m": entry.get("cache_creation_input_token_cost"),
        }
        prices = {
            k: float(v) * 1_000_000
            for k, v in prices.items()
            if isinstance(v, (int, float)) and v > 0
        }
        # 按张/按秒计价与 token 无关，**绝不能** ×1e6——那会把 $0.04/张
        # 变成 $40000/张。这类字段原值即最终值，单独取。
        #
        # 视频的每秒价上游有两套字段名：Google 系用 output_cost_per_second，
        # OpenAI/Runway 系用 output_cost_per_video_per_second，语义相同，
        # 这里归一到 per_second。
        #
        # ⚠️ 只在 mode 确实是生成类模型时才取这些字段。聊天模型不按张/按秒
        # 卖，它们身上出现的 output_cost_per_image 是上游标错的：实测
        # `gemini-3.1-pro-preview`(mode=chat) 的 output_cost_per_image=0.00012，
        # 而同族 `gemini-3-pro-image` 的按张价是 0.134、0.00012 恰是它的
        # output_cost_per_image_token —— 即图像 token 价被塞进了按张字段。
        # 不设这道门禁，$0.00012/张 会当成真实按张价流进通用表。
        mode = str(entry.get("mode") or "")
        GEN_MODES = {"image_generation", "image_edit", "video_generation"}
        # realtime 也算音频：它的钱全部走 *_cost_per_audio_token。
        AUDIO_MODES = {"audio_transcription", "audio_speech",
                       "audio_translation", "realtime"}
        # 按张价上游有两套字段名：多数条目写 output_cost_per_image，而 OpenAI
        # 直连的 `dall-e-2`/`dall-e-3` 写的是 input_cost_per_image（$0.02/$0.04）。
        # 只读 output_ 的话这两个**官方价拿不到**，表里只剩转售商 aiml 的
        # $0.026/$0.052，官方牌价一列会空着。
        # ⚠️ 这个字段只在 GEN_MODES 下才是「每张产出图」；chat 模型身上的
        #    input_cost_per_image 是「每张输入图」，语义完全不同，绝不能取。
        flat = {
            "per_image": (
                entry.get("output_cost_per_image")
                or entry.get("input_cost_per_image")
            ) if mode in GEN_MODES else None,
            "per_second": (
                entry.get("output_cost_per_second")
                or entry.get("output_cost_per_video_per_second")
            ) if mode in GEN_MODES else None,
        }
        flat = {
            k: float(v)
            for k, v in flat.items()
            if isinstance(v, (int, float)) and v > 0
        }
        # 生成类模型身上的**占位** token 价要丢掉。fireworks_ai 给旗下每个
        # image_generation 条目都填了同一个 input/output_cost_per_token=1.3e-10
        #（即 $0.00013/1M）——它按步数计费，token 价只是占位符。真实的图像
        # token 价在 $0.08/1M（digitalocean 的 sd3.5）到 $5/1M（gpt-image-1）
        # 这个量级，与占位值差着三个数量级，不会被这道门槛误伤。
        # 只在 GEN_MODES 下生效：聊天模型的低价是真价，不能碰。
        if mode in GEN_MODES:
            prices = {k: v for k, v in prices.items() if v > 0.001}
        prices.update(flat)

        # ── 音频专用计价键 ──
        # 与 GEN_MODES 同样是一道**模式门禁**：只有音频类 mode 才读这些键。
        # 不设门禁的话，聊天模型身上偶发的 output_cost_per_second 会被当成
        # 音频时长价——那正是 GEN_MODES 那段注释里已经踩过一次的同类坑。
        #
        # ⚠️ input_cost_per_second 与 output_cost_per_second 方向相反：
        #    转录类计的是**喂进去**的音频，合成类计的是**产出**的音频。
        #    两者都归一到每分钟，但 billing_basis 必须分开标，否则导出层
        #    会把 ASR 的价和 TTS 的价排进同一个 cheapest。
        #    实测 79 个 API 音频模型里 17 个按分钟，10 个输入 / 7 个产出。
        audio_basis = None
        audio_patch: dict = {}
        if mode in AUDIO_MODES:
            for src_key, basis in (
                ("input_cost_per_second", "input_audio"),
                ("output_cost_per_second", "output_audio"),
            ):
                value = entry.get(src_key)
                if (isinstance(value, (int, float)) and not isinstance(value, bool)
                        and value > 0 and "per_minute" not in prices):
                    prices["per_minute"], audio_patch = to_per_minute(
                        float(value), "second")
                    audio_basis = basis
            char = entry.get("input_cost_per_character")
            if (isinstance(char, (int, float)) and not isinstance(char, bool)
                    and char > 0):
                # 归一到每 100 万字符，与 input_per_1m 的 token 约定同构
                prices["per_1m_chars"] = float(char) * 1_000_000
            for src_key, target in (
                ("input_cost_per_audio_token", "audio_input_per_1m"),
                ("output_cost_per_audio_token", "audio_output_per_1m"),
            ):
                value = entry.get(src_key)
                if (isinstance(value, (int, float)) and not isinstance(value, bool)
                        and value > 0):
                    prices[target] = float(value) * 1_000_000
        if not prices:
            continue
        image_qualifier = _image_qualifier(key) if mode in GEN_MODES else None

        # 这里**故意不设 modality**。导出层按模态分组比价，而聚合器
        # （empiriolabs / vercel / ofox…）都不给这个字段、一律落在默认的
        # Text 组。若只给 litellm 标上 Image，两边就进了不同的池子：
        # 图像组非空 → 导出层的回退分支不触发 → 聚合器的报价被排除在
        # 「最低价」之外（实测 gpt-image-2 会漏掉 empiriolabs 的 $0.012，
        # 错报成 litellm 的 $0.054）。
        # 按张价靠 per_image 字段本身即可识别，无需 modality。

        model_id = key.split("/")[-1]
        seller = entry.get("litellm_provider") or (
            key.split("/")[0] if "/" in key else ""
        )
        # 部分第一方 key 只有裸模型名（如 embed-english-v3.0），公司信息
        # 只存在于 litellm_provider。此前没检查 seller，导致整组 Cohere
        # Embedding 价格在构造 PriceRecord 之前就被丢弃。
        company = (
            infer_company(key)
            or infer_company(model_id)
            or infer_company(seller)
            or _LITELLM_PROVIDER_COMPANIES.get(str(seller).lower())
            or (
                "Cohere"
                if model_id.lower().startswith("cohere.")
                else None
            )
        )
        if not company:
            continue

        # 同 models.dev：key 按服务方命名，首段是卖家。`litellm_provider` 更可靠
        # 时优先用它。卖家 == 厂商本人即为牌价。
        seller_company = (
            infer_company(seller)
            or _LITELLM_PROVIDER_COMPANIES.get(str(seller).lower())
        )
        is_first_party = bool(seller) and seller_company == company

        records.append(
            PriceRecord(
                source="litellm",
                source_tier=TIER_VENDORED,
                is_official=is_first_party,
                source_url=source_url,
                fetched_at=fetched_at,
                source_snippet=(
                    f"{key} input={entry.get('input_cost_per_token')} "
                    f"output={entry.get('output_cost_per_token')}"
                    + (f" image={entry.get('output_cost_per_image')}"
                       if "per_image" in flat else "")
                    + (f" second={flat['per_second']}"
                       if "per_second" in flat else "")
                    + (f" audio_sec_in={entry.get('input_cost_per_second')}"
                       f" audio_sec_out={entry.get('output_cost_per_second')}"
                       f" char={entry.get('input_cost_per_character')}"
                       if mode in AUDIO_MODES else "")
                ),
                # 溯源要求单位描述与实际取到的字段一致，不能一律写 per token
                unit_original=" + ".join(
                    filter(None, [
                        "per token (USD)" if any(
                            k.endswith("_per_1m") for k in prices) else "",
                        "per image (USD)" if "per_image" in flat else "",
                        "per second (USD)" if "per_second" in flat else "",
                        # 音频：写清楚是**哪一段**的分钟，光写 per minute
                        # 会让 ASR 与 TTS 的价看起来是同一个口径。
                        (f"per minute of {audio_basis or 'audio'} (USD)"
                         if "per_minute" in prices else ""),
                        ("per 1M characters (USD)"
                         if "per_1m_chars" in prices else ""),
                    ])
                ),
                source_version=source_version,
                model_id=model_id,
                # 只有生成类模型的前缀段才是质量/尺寸档；聊天模型的前缀
                # （`bedrock/ap-northeast-1/...`）是服务方，绝不能当档位。
                qualifier=image_qualifier,
                provider=f"litellm/{entry.get('litellm_provider') or '?'}",
                context_length=entry.get("max_input_tokens"),
                max_output=entry.get("max_output_tokens"),
                raw={
                    "company": company,
                    "weblink": LITELLM_WEBLINK,
                    "provider_name": "LiteLLM",
                    "mode": entry.get("mode"),
                    "litellm_key": key,
                    "seller": seller,
                    "seller_url": catalog_url_for(seller),
                    **({"derived_marker": DERIVED_MARKER,
                        "derived_note": _derived_note(
                            key, image_qualifier, flat["per_image"])}
                       if image_qualifier and "per_image" in flat else {}),
                    **audio_patch,
                },
                billing_basis=audio_basis,
                **prices,
            )
        )
    return records
