"""通用模型「开源权重且无任何报价」子表。

这批模型的价格是**客观不存在**（权重可自取），不是抓取失败；主表里它们
只能显示一整片空白。单独成表后主表保持原样不动，前端多一个子切换器。
与图像/视频的同名子表共用 PRICE_MODE_UNPRICED，规则完全一致。
"""
import csv
import tempfile
import unittest
from pathlib import Path

from src.export import (
    EXPORT_FUNCTIONS,
    PRICE_MODE_UNPRICED,
    _pool_for_price_mode,
    write_table,
)
from src.normalize import load_raw
from src.records import PriceRecord, TIER_OFFICIAL


ROOT = Path(__file__).resolve().parents[1]


def _record(model_id: str, **prices) -> PriceRecord:
    return PriceRecord(
        source="test_general_official",
        source_tier=TIER_OFFICIAL,
        is_official=True,
        source_url="https://example.com/pricing",
        fetched_at="2026-08-29T00:00:00Z",
        source_snippet=f"{model_id} test price",
        unit_original="per 1M tokens",
        model_id=model_id,
        provider="Test Provider",
        **prices,
    )


def _write(records_by_model):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "general_unpriced.csv"
        write_table(
            path,
            GeneralUnpricedTests.raw_models,
            {},
            records_by_model,
            export_functions=EXPORT_FUNCTIONS,
            price_mode=PRICE_MODE_UNPRICED,
        )
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))


def _write_main(records_by_model):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "general_main.csv"
        write_table(
            path,
            GeneralUnpricedTests.raw_models,
            {},
            records_by_model,
            export_functions=EXPORT_FUNCTIONS,
            exclude_unpriced_open_weight=True,
        )
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))


class GeneralUnpricedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw_models = load_raw(ROOT / "raw.csv")
        cls.open_weight = next(
            m for m in cls.raw_models
            if m.function in EXPORT_FUNCTIONS and m.availability.is_open_weight
        )
        cls.closed = next(
            m for m in cls.raw_models
            if m.function in EXPORT_FUNCTIONS and not m.availability.is_open_weight
        )

    def test_open_weight_without_any_price_is_included(self):
        models = {row["Model"] for row in _write({})}
        self.assertIn(self.open_weight.model, models)

    def test_open_weight_without_any_price_is_excluded_from_main(self):
        models = {row["Model"] for row in _write_main({})}
        self.assertNotIn(self.open_weight.model, models)

    def test_open_weight_with_price_stays_in_main(self):
        model = self.open_weight.model
        rows = _write_main({model: [_record(model, input_per_1m=0.5)]})
        self.assertIn(model, {row["Model"] for row in rows})

    def test_closed_weight_without_price_stays_in_main_as_gap(self):
        models = {row["Model"] for row in _write_main({})}
        self.assertIn(self.closed.model, models)

    def test_open_weight_with_a_price_is_excluded(self):
        model = self.open_weight.model
        rows = _write({model: [_record(model, input_per_1m=0.5)]})
        self.assertNotIn(model, {row["Model"] for row in rows})

    def test_closed_weight_without_price_is_a_real_gap_not_this_table(self):
        """闭源没抓到价 = not_found，是真缺口，混进来会让缺口看起来像免费。"""
        self.assertIsNone(
            _pool_for_price_mode([], PRICE_MODE_UNPRICED, is_open_weight=False)
        )
        models = {row["Model"] for row in _write({})}
        self.assertNotIn(self.closed.model, models)

    def test_no_price_columns_survive(self):
        """取价池按定义为空，价格列必然全空——全空列不导出。"""
        rows = _write({})
        self.assertTrue(rows)
        cols = set(rows[0])
        self.assertFalse(
            [c for c in cols if "per_1m" in c or "cheapest" in c or "quote_count" in c]
        )
        for col in ("Model", "Company", "Total Para", "is_open_weight", "price_status"):
            self.assertIn(col, cols)

    def test_every_row_is_open_weight_and_weights_free(self):
        rows = _write({})
        self.assertEqual({row["is_open_weight"] for row in rows}, {"1"})
        self.assertEqual({row["price_status"] for row in rows}, {"weights_free"})


if __name__ == "__main__":
    unittest.main()
