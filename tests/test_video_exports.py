import csv
import tempfile
import unittest
from pathlib import Path

from src.export import (
    PRICE_MODE_TOKEN,
    PRICE_MODE_UNPRICED,
    PRICE_MODE_VIDEO_COUNT,
    PRICE_MODE_VIDEO_TIME,
    _pool_for_price_mode,
    write_table,
    write_video_unit_table,
)
from src.normalize import load_raw
from src.records import PriceRecord, TIER_OFFICIAL


ROOT = Path(__file__).resolve().parents[1]


def _record(model_id: str, **prices) -> PriceRecord:
    return PriceRecord(
        source="test_video_official",
        source_tier=TIER_OFFICIAL,
        is_official=True,
        source_url="https://example.com/video-pricing",
        fetched_at="2026-08-28T00:00:00Z",
        source_snippet=f"{model_id} test video price",
        unit_original="test video unit",
        model_id=model_id,
        provider="Test Video Provider",
        **prices,
    )


class VideoExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw_models = load_raw(ROOT / "raw.csv")

    def test_same_model_can_enter_multiple_unit_tables(self):
        record = _record(
            "MiniMaxAI/MiniMax-H3",
            input_per_1m=1.0,
            per_call=0.05,
            per_second=0.08,
        )
        self.assertIsNotNone(
            _pool_for_price_mode([record], PRICE_MODE_TOKEN, is_open_weight=True)
        )
        self.assertIsNotNone(
            _pool_for_price_mode(
                [record], PRICE_MODE_VIDEO_COUNT, is_open_weight=True
            )
        )
        self.assertIsNotNone(
            _pool_for_price_mode(
                [record], PRICE_MODE_VIDEO_TIME, is_open_weight=True
            )
        )
        self.assertIsNone(
            _pool_for_price_mode(
                [record], PRICE_MODE_UNPRICED, is_open_weight=True
            )
        )

    def test_time_table_keeps_seconds_and_frames_as_distinct_rows(self):
        model = "MiniMaxAI/MiniMax-H3"
        records = {
            model: [
                _record(model, per_second=0.08),
                _record(model, per_frame=0.002),
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "video_time.csv"
            write_video_unit_table(
                path,
                self.raw_models,
                records,
                price_mode=PRICE_MODE_VIDEO_TIME,
            )
            with path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
        target = [row for row in rows if row["Model"] == model]
        self.assertEqual(
            {row["billing_unit"] for row in target},
            {"per_second", "per_frame"},
        )

    def test_open_weight_with_a_price_is_not_in_unpriced_table(self):
        model = "MiniMaxAI/MiniMax-H3"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "unpriced.csv"
            write_table(
                path,
                self.raw_models,
                {},
                {model: [_record(model, per_second=0.08)]},
                export_functions={"Video Generation"},
                price_mode=PRICE_MODE_UNPRICED,
            )
            with path.open(encoding="utf-8", newline="") as handle:
                models = {row["Model"] for row in csv.DictReader(handle)}
        self.assertNotIn(model, models)


if __name__ == "__main__":
    unittest.main()
