"""导出层：一张总表 + 一份源清单。

总表每行 = raw.csv 的一个模型，包含：
  - raw.csv 原样 7 列（便于和人工清单对照）
  - 规范化列
  - 价格 + 币种
  - **data_provider / provider_weblink / source_url / source_snippet**（溯源）
  - **price_status**：got / not_found，明确标注哪些没拿到
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path

from .display import display_name
from .records import PriceRecord

PRICE_FIELDS = [
    "input_per_1m",
    "output_per_1m",
    "cache_read_per_1m",
    "per_image",
    "per_video",
    "per_second",
]

COLUMNS = [
    # ── 展示用可读名。派生自 Model，仅供网页渲染；匹配/调用一律用 Model ──
    "display_name",
    # ── raw.csv 原样 ──
    "Model",
    "Company",
    "Function",
    "Total Para",
    "Activate Para",
    "On/Off Line",
    "Reasoning",
    # ── 规范化 ──
    "access_mode",
    "lifecycle",
    "is_open_weight",
    # ── 权重获取成本：与推理服务价正交的另一个维度 ──
    # free = 权重公开可自取，自部署成本只有算力；proprietary = 只能买 API
    "weights",
    # ── 价格状态 ──
    # price_status：哪些没获取到
    # price_kind：这个模型拿到的是厂商牌价还是只有第三方转售价
    "price_status",
    "price_kind",
    "currency",
    # ── 上下文长度。厂商按 prompt 长度分档定价时，一档一行 ──
    # 分层的写厂商官网原文（`<272K context length` / `≥ 200k prompt tokens`）；
    # 不分层的写该模型的上下文长度数值；都拿不到就留空，不猜。
    "context_tier",
    # ── 官方牌价的取值哨兵：got / weight open source / None ──
    "official_price",
    # ── 四组模态价，每组都是「官方价 + 全网最低价」──
    # 单位写在列名里，不另设单位列。一个模型可以同时有多组
    # （gpt-realtime-2.1 的 text $4 / audio $32 / image $5），有就填、没有就空。
    #
    # text：每 100 万 token 美元
    "text_official_input_per_1m_usd",
    "text_official_output_per_1m_usd",
    "text_official_provider",
    "text_official_source_url",
    "text_cheapest_input_per_1m_usd",
    "text_cheapest_input_seller",
    "text_cheapest_input_provider",
    "text_cheapest_input_source_url",
    "text_cheapest_output_per_1m_usd",
    "text_cheapest_output_seller",
    "text_cheapest_output_provider",
    "text_cheapest_output_source_url",
    "text_quote_count",
    # audio：同样按 token 计（OpenAI 的 audio token 与 text token 分开计价）
    "audio_official_input_per_1m_usd",
    "audio_official_output_per_1m_usd",
    "audio_official_provider",
    "audio_official_source_url",
    "audio_cheapest_input_per_1m_usd",
    "audio_cheapest_input_seller",
    "audio_cheapest_input_provider",
    "audio_cheapest_input_source_url",
    "audio_quote_count",
    # image：按张
    "image_official_per_image_usd",
    "image_official_provider",
    "image_official_source_url",
    "image_cheapest_per_image_usd",
    "image_cheapest_seller",
    "image_cheapest_provider",
    "image_cheapest_source_url",
    "image_quote_count",
    # video：按秒（不拿 per_video 折算——一条视频几秒是未知的）
    "video_official_per_second_usd",
    "video_official_provider",
    "video_official_source_url",
    "video_cheapest_per_second_usd",
    "video_cheapest_seller",
    "video_cheapest_provider",
    "video_cheapest_source_url",
    "video_quote_count",
    # 本行价格的抓取时间
    "fetched_at",
]

# price_status 的取值。not_found 曾把两种完全不同的情况混在一起：
#   weights_free —— 开源权重、没有任何托管方报价。**价格不存在，不是抓漏了**，
#                   想用直接下权重自己跑，成本是算力不是订阅。
#   not_found    —— 闭源且只能在线用，却没拿到价。这才是真缺口。
# 混在一起会让 139 个"本来就免费"的模型看起来像抓取失败。
STATUS_GOT = "got"
STATUS_WEIGHTS_FREE = "weights_free"
STATUS_NOT_FOUND = "not_found"

WEIGHTS_FREE = "free"
WEIGHTS_PROPRIETARY = "proprietary"


def _sort_key(record: PriceRecord) -> tuple:
    """取值优先级，从主到次：

    1. 币种：非美元的排最后（本表是美元表，欧元报价不能当美元用）
    2. 源层级：官方 > vendored > 第三方
    3. 厂商自己的牌价 > 平台转售价
    4. standard 层级优先（batch/flex 便宜、fast/priority 贵，都不是牌价）
    5. 无 qualifier 优先（长上下文/大 prompt 档是加价，不是牌价）
    6. 较新者优先
    7. model_id 字典序——只为让结果确定，避免同分时随输入顺序漂移
    """
    return (
        0 if record.currency == "USD" else 1,
        record.source_tier,
        0 if record.is_official else 1,
        0 if record.service_tier == "standard" else 1,
        0 if not record.qualifier else 1,
        # 多模态表按模态分行（Text/Audio/Image 各一条，价格差十几倍）。
        # 文本行是这个模型的"基准价"，优先取它，否则同一个模型的官方价
        # 会随源里的行序在 $2.5 和 $32 之间跳。
        0 if (record.modality or "Text").strip().lower() == "text" else 1,
        tuple(-ord(c) for c in record.fetched_at),
        record.model_id,
    )


def pick_best(records: list[PriceRecord]) -> PriceRecord | None:
    """从一个模型的多条观测里选出最能代表「牌价」的那条。

    只取一条而不是逐字段拼装，是为了让整行的溯源自洽——
    表里的价格和 source_snippet 必定来自同一条原始记录，可以直接核对。
    """
    priced = [r for r in records if r.input_per_1m or r.output_per_1m]
    pool = priced or records
    return sorted(pool, key=_sort_key)[0] if pool else None


# official_price 为空时的两种含义，必须分开写——都留空会把"客观不存在"
# 和"该有却没抓到"混成一件事：
#   weight open source —— 开源权重，厂商只放权重不卖服务，官方价**不存在**
#   None               —— 闭源在售却没拿到厂商牌价，这是**真缺口**
OFFICIAL_OPEN_WEIGHT = "weight open source"
OFFICIAL_NONE = "None"


def pick_official(records: list[PriceRecord]) -> PriceRecord | None:
    """厂商自己发布的牌价。没有就是没有——不拿转售价冒充。

    ⚠️ 判据用 has_any_price() 而非只看 token 价：图像/视频模型按张、按秒
    计价（`grok-imagine-image` 是 $0.02/张、`CogVideoX-3` 是 $0.2/条），
    只查 input/output 会把这些**已有官方价**的模型误判成缺失。
    真实事故：曾因此把闭源缺口从 38 个错报成 48 个。

    ⚠️ 与 cheapest 的口径差异（有意为之）：这里对 qualifier 只做**排序降权**
    而不排除，因为有些模型厂商只公布分档价（`gpt-5.5` 只有
    `<272K context length` 一档），排除掉就等于说它没有官方价。
    cheapest 那边则**必须**排除分档价——那是横向比价，混入加价档会失真。
    所以两列可能落在不同档上，各自的 source_snippet 都保留了原文可核对。
    """
    official = [
        r
        for r in records
        if r.is_official and r.currency == "USD" and r.has_any_price()
    ]
    return sorted(official, key=_sort_key)[0] if official else None


def comparable_quotes(records: list[PriceRecord]) -> list[PriceRecord]:
    """筛出**同口径**的报价，只有这些之间比价才有意义。

    不限定口径的"最便宜"是假象：
      - 只比 USD —— 欧元报价（Cortecs）换算前与美元不可比
      - 只比 standard —— batch 约半价、flex 更低，但那不是随时可用的价
      - 只比无 qualifier —— 长上下文档/大 prompt 档是另一种服务，不是同一个价
      - 只比文本模态 —— 见下

    ⚠️ **模态必须对齐**。OpenAI 的多模态表按模态分行，全部落进 input_per_1m：
        | gpt-audio-1.5 | Text  | $2.50  |
        | gpt-audio-1.5 | Audio | $32.00 |
    不区分就会拿 Text 价当"最低价"去比 Audio 官方价，得出 13 倍价差的假结论
    ——那不是某个卖家更便宜，只是换了个模态。多模态模型的音频/图像价另有
    audio_input_per_1m / per_image 等字段，不与文本价混比。
    """
    return [
        r
        for r in records
        if r.currency == "USD"
        and r.service_tier == "standard"
        and not r.qualifier
        and (r.modality or "Text").strip().lower() == "text"
    ]


def cheapest_by(
    records: list[PriceRecord], field: str
) -> PriceRecord | None:
    """在某个价格字段上最便宜的那条报价。

    输入价与输出价**分别**取最低，各自独立成列——它们经常来自不同卖家
    （A 家输入便宜、B 家输出便宜），合成一个"最低价"反而是个
    任何卖家都不提供的虚构组合。

    只有一个报价方时返回的就是那一条：单一报价虽无比较意义，
    但它确实是当前已知的最低价，留空反而丢信息（由 quote_count 标注可信度）。

    价格为 0 也是有效价（免费额度、Limited-time Free），所以判 None 而非 falsy。
    """
    pool = [r for r in comparable_quotes(records) if getattr(r, field) is not None]
    if not pool:
        return None
    # 同价时用 _sort_key 兜底，保证结果稳定、不随输入顺序漂移
    return min(pool, key=lambda r: (getattr(r, field), _sort_key(r)))


def count_quotes(records: list[PriceRecord]) -> int:
    """有几个**独立报价方**参与了比价。

    按 (source, provider) 去重：同一模型在一个源里往往有几十条观测
    （不同区域、不同快照），不去重会把"1 个卖家"报成 30 个，
    让 cheapest 看起来比实际更有依据。
    """
    return len({(r.source, r.provider) for r in comparable_quotes(records)})


def _seller_of(record: PriceRecord) -> str:
    """报这个价的卖家。聚合器有 seller 段就用它，否则退回 provider。"""
    return record.raw.get("seller") or record.provider


def _provider_of(record: PriceRecord) -> str:
    """这个数字是**哪个数据源**给的（models.dev / LiteLLM / 厂商官方文档…）。

    与 seller 是两回事：seller 是收钱的人（`nano-gpt`），
    provider 是我们从哪儿读到这个价（`models.dev`）。
    """
    return record.raw.get("provider_name") or record.provider


def _is_modality(record: PriceRecord, name: str) -> bool:
    """这条观测属于哪个模态。

    厂商官方表按模态分行，modality 列写着 Text/Audio/Image；聚合器不给这个
    字段，默认按文本算（它们收录的绝大多数是文本模型）。
    """
    return (record.modality or "Text").strip().lower() == name


def modality_official(
    records: list[PriceRecord], modality: str, field: str
) -> PriceRecord | None:
    """某个模态下的官方价。"""
    pool = [
        r
        for r in records
        if r.is_official
        and r.currency == "USD"
        and _is_modality(r, modality)
        and getattr(r, field, None) is not None
    ]
    return sorted(pool, key=_sort_key)[0] if pool else None


def modality_cheapest(
    records: list[PriceRecord], modality: str, field: str
) -> PriceRecord | None:
    """某个模态下的全网最低价。

    只在**同模态、同单位**的报价之间排序：按张价与按 token 价没有换算关系，
    混比得出的"最低"没有意义。单位不符的源仍保留在 out/sources.md，
    只是值不参与统计。
    """
    pool = [
        r
        for r in records
        if r.currency == "USD"
        and r.service_tier == "standard"
        and not r.qualifier
        and _is_modality(r, modality)
        and getattr(r, field, None) is not None
    ]
    if not pool:
        return None
    return min(pool, key=lambda r: (getattr(r, field), _sort_key(r)))


def modality_quotes(records: list[PriceRecord], modality: str, field: str) -> int:
    """该模态下有几个独立报价方给出了这个单位的价格。"""
    return len(
        {
            (r.source, r.provider)
            for r in records
            if r.currency == "USD"
            and r.service_tier == "standard"
            and not r.qualifier
            and _is_modality(r, modality)
            and getattr(r, field, None) is not None
        }
    )


def _common_context(records: list[PriceRecord]) -> int | None:
    """该模型的上下文长度：全部观测里出现最多的那个值。

    不同卖家报的值可能不同（有的平台把上下文限得更短），取众数比取第一条
    更接近模型的真实规格。全都没有就返回 None——留空，不猜。
    同频时取较大者，避免结果随输入顺序漂移。
    """
    counts = defaultdict(int)
    for r in records:
        if r.context_length:
            counts[r.context_length] += 1
    if not counts:
        return None
    return max(counts, key=lambda v: (counts[v], v))


# 分层限定里的上下文阈值：`<272K context length` / `≥ 200k prompt tokens`
_TIER_TOKENS = re.compile(r"([0-9][0-9,\.]*)\s*([kKmM])?\s*(?:context|prompt|token)", re.I)


def parse_tier_tokens(qualifier: str | None) -> int | None:
    """从分层描述里解析出 token 阈值，解析不出返回 None（不猜）。

    `<272K context length`   → 272000
    `≥ 200k prompt tokens`   → 200000
    `long context`           → None（没有数值，只是相对描述）

    ⚠️ `<272K context length; long context` 是**长上下文档**（272K 以上），
    阈值 272000 属于前半句描述的另一档。这种复合标签解析出的数字会误导
    ——填 272000 会让人以为这档上限是 272K，恰好相反。所以直接返回 None，
    由 tier_label 原文说明，不做可能出错的推断。
    """
    if not qualifier:
        return None
    if "long context" in qualifier.lower():
        return None
    match = _TIER_TOKENS.search(qualifier)
    if not match:
        return None
    try:
        number = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    suffix = (match.group(2) or "").lower()
    if suffix == "k":
        number *= 1_000
    elif suffix == "m":
        number *= 1_000_000
    return int(number)


def official_tiers(records: list[PriceRecord]) -> list[PriceRecord]:
    """厂商按**上下文长度**分档公布的官方价，一档一条。

    `gpt-5.5` 的短/长上下文是两个价（$5.00 / $10.00），只报一个等于
    把另一档藏起来。

    只收 standard 服务档：batch/flex/fast 是"怎么跑"（异步、闲置算力、
    优先通道），与上下文分层是两回事，混进来会让同一个上下文档出现
    好几个价。
    """
    official = [
        r
        for r in records
        if r.is_official
        and r.currency == "USD"
        and r.has_any_price()
        and r.service_tier == "standard"
        and (r.modality or "Text").strip().lower() == "text"
    ]
    best_of: dict[str | None, PriceRecord] = {}
    for rec in official:
        current = best_of.get(rec.qualifier)
        if current is None or _sort_key(rec) < _sort_key(current):
            best_of[rec.qualifier] = rec
    # 短档在前、长档在后；无分层的排最前
    return sorted(
        best_of.values(),
        key=lambda r: (
            1 if r.qualifier else 0,
            1 if "long" in (r.qualifier or "").lower() else 0,
            parse_tier_tokens(r.qualifier) or 0,
            r.qualifier or "",
        ),
    )


def _fmt(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


# 只导出这些 Function 的模型。抓取、匹配、数据源清单**一律不受影响**——
# 这纯粹是导出层的显示过滤，改回全量只需把它设成 None。
# 其他能力的模型（语音/图像/视频/嵌入…）的价格照常抓取并计入 out/sources.md，
# 只是暂不进总表。
EXPORT_FUNCTIONS: set[str] | None = {"General-Purpose"}


def write_table(
    path: Path,
    raw_models: list,
    best_by_model: dict[str, PriceRecord],
    records_by_model: dict[str, list[PriceRecord]] | None = None,
) -> dict:
    """records_by_model 是该模型的**全部**观测（未收敛成一条）。

    official_price 必须从全部观测里找，不能从 best 判断：pick_best 选的是
    最权威的一条，但"最权威"经常就是转售价（开源模型压根没有官方价可选）。
    """
    records_by_model = records_by_model or {}
    stats = defaultdict(int)
    # 先攒在内存里：全空的列要等所有行都生成完才能判定，不能边写边判。
    # 492 行 × 56 列的规模，攒下来毫无压力。
    buffered: list[list[str]] = []
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        for raw in raw_models:
            # 显示过滤：只导出指定 Function。被跳过的模型仍然完成了抓取与匹配，
            # 其价格照常计入 out/sources.md 的源统计，只是不进总表。
            if EXPORT_FUNCTIONS is not None and raw.function not in EXPORT_FUNCTIONS:
                stats["hidden_by_function"] += 1
                continue

            best = best_by_model.get(raw.model)
            av = raw.availability
            pool = records_by_model.get(raw.model, [])
            official = pick_official(pool)

            # ── audio / image / video 三组：与 text 同样是「官方价 + 最低价」，
            #    单位各自固定（audio 按 token、image 按张、video 按秒）。
            #    一个模型可以同时有多组，互不覆盖。
            def _group(modality: str, field: str, with_output: bool) -> list[str]:
                """一组模态价：官方（价 + provider + url）+ 最低（价 + seller +
                provider + url）+ 报价方数量。

                with_output 只对 audio 为真——它按 token 计价，有输入/输出两个
                维度；image/video 按张/按秒，只有一个价格维度。
                """
                off = modality_official(pool, modality, field)
                cheap = modality_cheapest(pool, modality, field)
                n = modality_quotes(pool, modality, field)
                cells = [_fmt(getattr(off, field)) if off else ""]
                if with_output:
                    # 输出价取**同一条**官方记录的，保证同一档内自洽
                    cells.append(_fmt(off.output_per_1m) if off else "")
                cells += [
                    _provider_of(off) if off else "",
                    off.source_url if off else "",
                    _fmt(getattr(cheap, field)) if cheap else "",
                    _seller_of(cheap) if cheap else "",
                    _provider_of(cheap) if cheap else "",
                    cheap.source_url if cheap else "",
                    str(n) if n else "",
                ]
                return cells

            audio_cells = _group("audio", "input_per_1m", with_output=True)
            image_cells = _group("image", "per_image", with_output=False)
            video_cells = _group("video", "per_second", with_output=False)

            # image/video 的按次价常没有 modality 列（聚合器不给），
            # 靠字段本身识别：有 per_image 就是按张价，有 per_second 就是按秒价。
            if not any(image_cells[:1] + image_cells[3:4]):
                image_cells = _group("text", "per_image", with_output=False)
            if not any(video_cells[:1] + video_cells[3:4]):
                video_cells = _group("text", "per_second", with_output=False)

            # ── text 组：输入、输出各自取最低（常来自不同卖家）──
            cheap_in = cheapest_by(pool, "input_per_1m")
            cheap_out = cheapest_by(pool, "output_per_1m")
            quotes = count_quotes(pool)
            text_cheapest = [
                _fmt(cheap_in.input_per_1m) if cheap_in else "",
                _seller_of(cheap_in) if cheap_in else "",
                _provider_of(cheap_in) if cheap_in else "",
                cheap_in.source_url if cheap_in else "",
                _fmt(cheap_out.output_per_1m) if cheap_out else "",
                _seller_of(cheap_out) if cheap_out else "",
                _provider_of(cheap_out) if cheap_out else "",
                cheap_out.source_url if cheap_out else "",
                str(quotes) if quotes else "",
            ]
            if quotes == 1:
                stats["cheapest_single_quote"] += 1
            elif quotes > 1:
                stats["cheapest_compared"] += 1

            # 厂商公布的全部上下文档位。有多档就展开成多行。
            tiers = official_tiers(pool)
            if official is not None:
                stats["official_price_got"] += 1
                if len(tiers) > 1:
                    stats["multi_tier_models"] += 1
            else:
                stats[
                    "official_open_weight"
                    if av.is_open_weight
                    else "official_none"
                ] += 1

            def _official_cells(tier: PriceRecord | None) -> list[str]:
                """official_price 哨兵 + text 组的官方价四列。"""
                if tier is not None:
                    return [
                        "got",
                        _fmt(tier.input_per_1m),
                        _fmt(tier.output_per_1m),
                        _provider_of(tier),
                        tier.source_url,
                    ]
                sentinel = (
                    OFFICIAL_OPEN_WEIGHT if av.is_open_weight else OFFICIAL_NONE
                )
                return [sentinel, "", "", "", ""]

            def _context_tier(tier: PriceRecord | None) -> list[str]:
                """上下文长度这一格。

                分层的写**厂商官网原文**（`<272K context length` /
                `≥ 200k prompt tokens` / `long context`），两家写法不同也照原文，
                因为改写就不再是"官网怎么写"了。
                不分层的写该模型的上下文长度数值；两者都没有就留空，不猜。
                """
                if tier is not None and tier.qualifier:
                    return [tier.qualifier]
                # 厂商定价表往往不含上下文长度（OpenAI 的就没有），
                # 得从该模型的**全部**观测里找——聚合器普遍带这个字段。
                # 取众数而非第一条：不同卖家可能报不同值（同一模型在某些
                # 平台被限流到更短上下文），多数派更可能是模型的真实规格。
                ctx = (tier.context_length if tier else None) or _common_context(pool)
                return [_fmt(ctx)]

            weights = WEIGHTS_FREE if av.is_open_weight else WEIGHTS_PROPRIETARY
            # 前缀区别于 price_status 的 weights_free，避免两个计数键撞名
            stats[f"w_{weights}"] += 1

            common = [
                display_name(raw.model, raw.company),
                raw.model, raw.company, raw.function,
                _fmt(raw.total_params_b), _fmt(raw.active_params_b),
                av.raw, "Yes" if raw.reasoning else "No",
                av.access_mode, av.lifecycle,
                "1" if av.is_open_weight else "0",
                weights,
            ]

            if best is None:
                # 开源权重却没有任何托管报价 ≠ 抓取失败。权重本身就是免费的，
                # 分开标注才不会把"不存在的价格"读成"缺失的数据"。
                status = (
                    STATUS_WEIGHTS_FREE if av.is_open_weight else STATUS_NOT_FOUND
                )
                stats[status] += 1
                # 没有服务价，但官方价可能仍然存在（厂商公布了牌价却无人转售）
                prefix = common + [status, "", ""]
            else:
                stats["got"] += 1
                stats["official" if best.is_official else "hosted"] += 1
                prefix = common + [
                    STATUS_GOT,
                    # 这个模型拿到的是厂商牌价还是只有第三方转售价
                    "official" if best.is_official else "hosted",
                    best.currency,
                ]

            # 每个上下文档位一行；没有官方价的模型仍出一行（携带哨兵）
            for tier in tiers or [None]:
                # 本行价格的抓取时间：优先取本档位自己的，退回 best 或最低价。
                # 分层展开后每行的价格来源不同，共用一个时间会不准。
                stamped = tier or best or cheap_in or cheap_out
                # official_price 哨兵要排在 context_tier 之后（见 COLUMNS），
                # 所以 _official_cells 的首元素单独取出。
                oc = _official_cells(tier)
                row = (
                    prefix
                    + _context_tier(tier)
                    + [oc[0]]
                    + oc[1:]
                    + text_cheapest
                    + audio_cells
                    + image_cells
                    + video_cells
                    + [stamped.fetched_at[:10] if stamped else ""]
                )
                # 列数错位是静默事故：CSV 照样写出，只是从某一列起全部右移，
                # 表面完全正常。加列时手数空串已经错过两次，这里断言兜底。
                if len(row) != len(COLUMNS):
                    raise ValueError(
                        f"行列数不匹配：{len(row)} != {len(COLUMNS)}"
                        f"（model={raw.model!r}）"
                    )
                buffered.append(row)
                stats["rows"] += 1

        # 全空的列不导出。当前只显示 General-Purpose，audio/image/video 三组
        # 25 列必然全空（那些价格属于被过滤掉的 Function），留着纯占位。
        # ⚠️ 列集合会随 EXPORT_FUNCTIONS 变化，前端不能假定列固定存在。
        keep = [
            i
            for i, _ in enumerate(COLUMNS)
            if any(row[i].strip() for row in buffered)
        ]
        stats["columns_dropped"] = len(COLUMNS) - len(keep)
        stats["columns"] = len(keep)
        writer.writerow([COLUMNS[i] for i in keep])
        for row in buffered:
            writer.writerow([row[i] for i in keep])
    return dict(stats)


def write_sources_md(
    path: Path,
    fetches: list[dict],
    counts: dict,
    parse_warnings: list[str] | None = None,
) -> None:
    """源清单：每个数据 provider 的名字、weblink、许可、本次产出。

    末尾附解析告警。这不是可有可无的装饰——厂商改一个列头就会静默丢掉一个
    价格维度（Moonshot 的 `Input Price` 就中过一次，那批模型只剩输出价，
    表面上一切正常）。告警可见是唯一的发现途径。
    """
    lines = [
        "# 数据源清单",
        "",
        f"由 `update_prices.py` 于 {fetches[0]['fetched_at'][:19] if fetches else '—'} 自动生成。",
        "",
        "| 数据 Provider | 网页 | 抓取地址 | 许可 | 状态 | 记录数 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for f in sorted(fetches, key=lambda x: (not x["ok"], x["source"])):
        status = "✅" if f["ok"] else f"❌ {f.get('error_kind') or ''}"
        weblink = f.get("weblink") or ""
        lines.append(
            f"| {f.get('provider_name') or f['source']} | {weblink} | `{f['url'][:70]}` | "
            f"{f.get('license') or '—'} | {status} | {counts.get(f['source'], 0)} |"
        )
    lines += [
        "",
        "## 说明",
        "",
        "- **权重 (weights)**：`free` = 权重公开可自取，自部署成本只有算力；"
        "`proprietary` = 只能购买 API。这与下面的服务价是**两个正交维度**——"
        "开源权重模型照样可以有 API 报价，那是别人替你部署的服务费，不是权重的价。",
        "- **`price_status = weights_free`**：开源权重且无任何托管方报价。"
        "**价格不存在，不是抓漏了**；想用直接下权重自己跑。",
        "- **官方价 (official)**：模型厂商自己发布的牌价（含厂商自营 API）。",
        "- **托管价 (hosted)**：第三方转售该模型的价格，通常与牌价不同"
        "（实测 Bedrock 上的 Claude 普遍比 Anthropic 官方贵约 10%）。",
        "- **`hosted_seller`**：实际报这个价的卖家。同一个开源模型在不同平台"
        "价差可达十几倍（gemma-3 从 $0.05 到 $0.65），不看卖家无法判断代表性。",
        "- 价格统一换算为**每 100 万 token 美元**；图像/视频/秒按次计价另列。",
        "  `source_snippet` 保留产出该数字的原文，任何数字存疑可直接核对。",
        "- ⚠️ Cortecs 报价为**欧元**，选价时已排除，不与美元混用。",
        "",
        "## 关于 models.dev 与 LiteLLM",
        "",
        "这两个是 MIT 许可的开源数据集，已 vendor 到本地 `vendor/`，",
        "上游站点消失也不影响使用（只是停止获得更新）。",
        "",
        "需要说明的是它们**本身就是根源**，无法\"从更上游自己获取\"：",
        "models.dev 的 `google.ts` 里写着 `cost: existing.cost`（价格取自手工维护的 TOML），",
        "并注明 Google 的 Models API 不提供价格；LiteLLM 的 JSON 由人和 AI bot",
        "依据厂商公告手工维护。厂商侧确实不存在可抓的价格 API。",
        "",
    ]

    if parse_warnings:
        lines += [
            "## 解析告警",
            "",
            "厂商改一个列头就会静默丢掉一个价格维度，所以认不出的东西必须报出来。",
            "",
        ]
        lines += [f"- {w}" for w in parse_warnings]
        lines.append("")
    else:
        lines += ["## 解析告警", "", "本次无告警。", ""]

    path.write_text("\n".join(lines), encoding="utf-8")
