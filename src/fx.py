"""用欧洲央行每日参考汇率把非美元报价换算成美元。

ECB 的 XML 以 EUR 为基准（1 EUR = N 单位目标货币）。因此：
    1 source_currency = USD_rate / source_currency_rate USD

原币金额和汇率信息写入 PriceRecord.raw；价格字段改成 USD 后才进入匹配、
官方价选择和最低价比较。这样既能正常排序，也能完整追回换算过程。
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from .http import fetch
from .records import PriceRecord

ECB_DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
ECB_WEBLINK = (
    "https://data.ecb.europa.eu/key-figures/"
    "ecb-interest-rates-and-exchange-rates/exchange-rates"
)
FX_MARKER = "⇄"
MAX_CACHE_AGE_DAYS = 7


@dataclass(frozen=True)
class FxSnapshot:
    as_of: str
    fetched_at: str
    rates_per_eur: dict[str, float]
    source_url: str = ECB_DAILY_URL

    def usd_per(self, currency: str) -> float | None:
        """返回 1 单位 currency 等于多少 USD。"""
        code = currency.strip().upper()
        if code == "USD":
            return 1.0
        source_rate = 1.0 if code == "EUR" else self.rates_per_eur.get(code)
        usd_rate = self.rates_per_eur.get("USD")
        if source_rate is None or usd_rate is None or source_rate <= 0:
            return None
        return usd_rate / source_rate


def _parse_xml(text: str, fetched_at: str) -> FxSnapshot:
    root = ET.fromstring(text)
    as_of = ""
    rates: dict[str, float] = {}
    for element in root.iter():
        if element.tag.endswith("Cube") and element.attrib.get("time"):
            as_of = element.attrib["time"]
        currency = element.attrib.get("currency", "").upper()
        rate = element.attrib.get("rate")
        if currency and rate:
            rates[currency] = float(rate)
    if not as_of or "USD" not in rates:
        raise ValueError("ECB XML 缺少日期或 USD 汇率")
    return FxSnapshot(as_of=as_of, fetched_at=fetched_at, rates_per_eur=rates)


def _load_cache(path: Path) -> FxSnapshot | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        snapshot = FxSnapshot(
            as_of=payload["as_of"],
            fetched_at=payload["fetched_at"],
            rates_per_eur={
                str(k).upper(): float(v)
                for k, v in payload["rates_per_eur"].items()
            },
            source_url=payload.get("source_url") or ECB_DAILY_URL,
        )
        utc_today = datetime.now(timezone.utc).date()
        age = (utc_today - date.fromisoformat(snapshot.as_of)).days
        return snapshot if 0 <= age <= MAX_CACHE_AGE_DAYS else None
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _save_cache(path: Path, snapshot: FxSnapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "European Central Bank",
        "source_url": snapshot.source_url,
        "weblink": ECB_WEBLINK,
        "base": "EUR",
        "as_of": snapshot.as_of,
        "fetched_at": snapshot.fetched_at,
        "rates_per_eur": dict(sorted(snapshot.rates_per_eur.items())),
        "marker": FX_MARKER,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_ecb_rates(
    cache_path: Path, *, save: bool = True
) -> tuple[FxSnapshot | None, dict, list[str]]:
    """每次先请求 ECB；失败时只接受不超过 7 天的仓库缓存。"""
    result = fetch(ECB_DAILY_URL, use_cache=False)
    warnings: list[str] = []
    snapshot: FxSnapshot | None = None
    from_fallback = False

    if result.ok:
        try:
            snapshot = _parse_xml(result.text, result.fetched_at)
            if save:
                _save_cache(cache_path, snapshot)
        except (ET.ParseError, ValueError) as exc:
            warnings.append(f"ecb_fx: 汇率 XML 解析失败：{exc}")

    if snapshot is None:
        snapshot = _load_cache(cache_path)
        from_fallback = snapshot is not None
        if snapshot is not None:
            warnings.append(
                f"ecb_fx: 本次联网获取失败，沿用 {snapshot.as_of} 的 ECB 缓存汇率"
            )
        else:
            warnings.append(
                "ecb_fx: 无法取得 ECB 汇率，且没有 7 天内缓存；"
                "非美元报价本次不参与美元价格选择"
            )

    entry = {
        "source": "ecb_fx",
        "url": ECB_DAILY_URL,
        "ok": snapshot is not None,
        "status": result.status,
        "version": snapshot.as_of if snapshot else None,
        "fetched_at": result.fetched_at
        or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "from_cache": from_fallback,
        "error": None if snapshot else result.error,
        "error_kind": None if snapshot else (result.error_kind or "fx_unavailable"),
        "n_records": 0,
        "provider_name": "European Central Bank reference rates",
        "weblink": ECB_WEBLINK,
        "license": "ECB reference rates",
    }
    return snapshot, entry, warnings


def convert_records_to_usd(
    records: list[PriceRecord], snapshot: FxSnapshot | None
) -> tuple[int, set[str]]:
    """原地换算所有可支持的非美元记录，并返回数量与未支持币种。"""
    if snapshot is None:
        return 0, {
            r.currency.strip().upper()
            for r in records
            if r.currency.strip().upper() != "USD"
        }

    converted = 0
    unsupported: set[str] = set()
    for record in records:
        currency = record.currency.strip().upper()
        if currency == "USD":
            continue
        rate = snapshot.usd_per(currency)
        if rate is None:
            unsupported.add(currency)
            continue

        originals = {
            field: getattr(record, field)
            for field in PriceRecord.PRICE_FIELDS
            if getattr(record, field) is not None
        }
        for field, value in originals.items():
            setattr(record, field, value * rate)
        record.raw.update(
            {
                "fx_converted": True,
                "fx_marker": FX_MARKER,
                "fx_original_currency": currency,
                "fx_original_prices": originals,
                "fx_rate_to_usd": rate,
                "fx_rate_date": snapshot.as_of,
                "fx_source_url": snapshot.source_url,
            }
        )
        record.currency = "USD"
        converted += 1
    return converted, unsupported


def fx_cells(record: PriceRecord | None, field: str) -> list[str]:
    """导出网页所需的标记、可读换算说明和 ECB 出处。"""
    if record is None or not record.raw.get("fx_converted"):
        return ["", "", ""]
    originals = record.raw.get("fx_original_prices") or {}
    original = originals.get(field)
    converted = getattr(record, field, None)
    if original is None or converted is None:
        return ["", "", ""]
    currency = record.raw["fx_original_currency"]
    rate = record.raw["fx_rate_to_usd"]
    as_of = record.raw["fx_rate_date"]
    note = (
        f"{currency} {original:g} → USD {converted:g}; "
        f"1 {currency} = {rate:.8g} USD (ECB {as_of})"
    )
    return [
        record.raw.get("fx_marker") or FX_MARKER,
        note,
        record.raw.get("fx_source_url") or ECB_DAILY_URL,
    ]
