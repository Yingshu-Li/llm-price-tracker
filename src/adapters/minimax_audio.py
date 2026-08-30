"""MiniMax 语音模型官方价（Tier 1 官方，人民币）。

正确的按量计费页是 ``/docs/guides/pricing-paygo``——旧的
``/document/price`` 已经返回 500。该页服务端渲染，curl 直接可取（368KB）。

    speech-2.8-hd      3.5 元/万字符   （T2A 与 T2A Async 同价）
    speech-2.8-turbo   2   元/万字符

⚠️ **必须校验表头单位**。价格数字本身看不出量纲——若哪天从「元/万字符」
   改成「元/千字符」，照旧取 3.5 就是 10 倍错误，而 3.5 这个数字完全正常。
   所以表头对不上时**报警并放弃**，不猜。

⚠️ 音色设计 / 快速复刻的 9.9 元/**音色** 故意不收：那是音色授权费，
   与「每次调用多少钱」不是同一件事，混进按次表会让「最低价」失去意义。
   同理，语音资源包（¥700 / 200 万字符）是预付套餐不是牌价，也不收。

⚠️ 音乐接口已于 2026-08-20 停止对新用户服务（页面原文：「付费接口
   （音乐生成、歌词生成）不再面向新用户提供服务」），因此**不再解析**
   Music-3.0 / Music-2.6 的 1.0 元/首——那是历史价，不是在售牌价。

金额是人民币，交给 convert_records_to_usd 走 ECB 汇率统一换算（⇄ 标记）。
"""

from __future__ import annotations

import html as html_mod
import re

from ..records import PriceRecord, TIER_OFFICIAL

MINIMAX_URL = "https://platform.minimaxi.com/docs/guides/pricing-paygo"
MINIMAX_WEBLINK = MINIMAX_URL

# 人工核验过、且 raw.csv 有对应行的型号
_SUPPORTED = {"speech-2.8-hd", "speech-2.8-turbo"}

# 表头必须出现这个单位，否则不取数
_UNIT_HEADER = "元/万字符"

_MODEL_RE = re.compile(r"\b(speech-2\.8-(?:hd|turbo))\b")
_NUM_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)$")


def _to_cells(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " | ", fragment)
    text = html_mod.unescape(text)
    text = re.sub(r"(\s*\|\s*)+", " | ", text)
    return re.sub(r"[ \t]+", " ", text)


def parse_minimax_audio(
    text: str, *, source_url: str, fetched_at: str, source_version: str | None
) -> tuple[list[PriceRecord], list[str]]:
    warnings: list[str] = []
    records: list[PriceRecord] = []
    cells_text = _to_cells(text)

    if _UNIT_HEADER not in cells_text.replace(" ", ""):
        return [], [
            f"minimax_audio: 页面里找不到表头单位「{_UNIT_HEADER}」，"
            "疑似 MiniMax 改了计价口径。已放弃取数——数字本身看不出量纲，"
            "猜错就是 10 倍静默错误。"
        ]

    seen: set[str] = set()
    for match in _MODEL_RE.finditer(cells_text):
        model_id = match.group(1)
        if model_id not in _SUPPORTED or model_id in seen:
            continue
        # 该行「单价」是模型 id 之后的第一个纯数字单元格
        tail = [c.strip() for c in cells_text[match.end(): match.end() + 700].split("|")]
        price = None
        for cell in tail:
            hit = _NUM_RE.match(cell)
            if hit:
                price = float(hit.group(1))
                break
        if price is None:
            warnings.append(
                f"minimax_audio: {model_id} 后面没找到数字单价，表结构可能变了")
            continue

        seen.add(model_id)
        records.append(PriceRecord(
            source="minimax_audio_official",
            source_tier=TIER_OFFICIAL,
            is_official=True,
            source_url=MINIMAX_WEBLINK,
            fetched_at=fetched_at,
            source_snippet=f"{model_id} | {price} {_UNIT_HEADER}",
            unit_original="CNY per 10k characters",
            model_id=model_id,
            provider="MiniMax",
            currency="CNY",
            # 万字符 -> 百万字符
            per_1m_chars=price * 100,
            source_version=source_version,
            raw={
                "company": "MiniMax",
                "provider_name": "MiniMax",
                "weblink": MINIMAX_WEBLINK,
                "seller_url": MINIMAX_WEBLINK,
            },
        ))

    if not records:
        warnings.append(
            "minimax_audio: 一条语音价都没解析到，疑似页面结构或型号命名变了。")
    return records, warnings
