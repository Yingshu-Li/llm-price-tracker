"""音频时长价的刻度归一。

为什么可以归一：时 / 分 / 秒是**同一量纲**，×60 是定义式换算，不是
「折算等于编数据」里禁止的跨单位折算（那说的是 token 价与按张价之间
没有固定比率）。仓库里已有先例：``sensenova.py`` 读到每分钟价后 ``/60``
存进 ``per_second``。

为什么归一到分钟：厂商官方页印「per minute」的最多（OpenAI 转录全系、
Deepgram STT、ElevenLabs、Mistral、DeepInfra 都是），且数值落在
$0.0045–$2.20 这个最好读的区间；按秒会有四五个前导零，按小时又太粗。

⚠️ 归一**只解决刻度，不解决可比性**。计量对象不同的价格即便都换成分钟
也不能同列比价，那由 ``PriceRecord.billing_basis`` 区分：
  input_audio / output_audio / session
"""
from __future__ import annotations

# 与汇率的 ⇄、数据源换算的 ≈ 并列的第三个标记。
# 含义不同，绝不能混用：
#   ⇄ 原始报价不是美元，按 ECB 汇率换算
#   ≈ 此价由**数据源**替厂商换算得出，不是厂商牌价
#   ⏱ 厂商牌价本身，只是把时间刻度换成了分钟
TIME_MARKER = "⏱"

# 每 1 分钟等于多少个该单位
_PER_MINUTE = {
    "second": 60.0,
    "minute": 1.0,
    "hour": 1.0 / 60.0,
}

_LABEL = {"second": "秒", "minute": "分钟", "hour": "小时"}

BILLING_BASES = ("input_audio", "output_audio", "session")


def to_per_minute(value: float, unit: str) -> tuple[float, dict]:
    """把 value（每 <unit> 美元）换成每分钟美元。

    返回 (每分钟价, raw 补丁)。raw 补丁里带标记与可读说明；单位本就是
    分钟时不打标记——没换算就不该有「已换算」的提示。
    """
    if unit not in _PER_MINUTE:
        raise ValueError(
            f"未知时间单位 {unit!r}，只接受 {tuple(_PER_MINUTE)}。"
            "不认识的单位必须显式加进来，不能默默当成分钟。"
        )
    per_minute = float(value) * _PER_MINUTE[unit]
    if unit == "minute":
        return per_minute, {}
    return per_minute, {
        "time_marker": TIME_MARKER,
        "time_note": f"原始报价 ${_fmt(value)} / {_LABEL[unit]}",
        "time_original_value": float(value),
        "time_original_unit": unit,
    }


def _fmt(v: float) -> str:
    """价格数字的可读写法：去掉浮点噪声，但不丢有效位。"""
    s = f"{v:.10f}".rstrip("0").rstrip(".")
    return s or "0"
