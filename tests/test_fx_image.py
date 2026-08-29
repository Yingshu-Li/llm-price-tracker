"""人民币按张价的汇率换算与溯源链路。

火山引擎、阿里云的图像模型报的是「元/张」。换算逻辑本身是通用的
（convert_records_to_usd 遍历 PriceRecord.PRICE_FIELDS，per_image 在其中），
真正容易掉的是**溯源**：换算完只剩一个美元数字，读者无从判断它是牌价
还是折算值。这组测试锁住 image 组的 fx 三列确实会被填上。
"""

import unittest
from pathlib import Path

from src.export import COLUMNS, write_table
from src.fx import FxSnapshot, convert_records_to_usd, fx_cells
from src.normalize import load_raw
from src.records import PriceRecord, TIER_OFFICIAL

ROOT = Path(__file__).resolve().parent.parent


def _cny_image_record(model_id: str, per_image: float) -> PriceRecord:
    return PriceRecord(
        source="volcengine_official_verified",
        source_tier=TIER_OFFICIAL,
        is_official=True,
        source_url="https://www.volcengine.com/product/doubao",
        fetched_at="2026-08-28T00:00:00+00:00",
        source_snippet="Doubao-Seedream | 0.259元/张",
        unit_original="CNY per image",
        model_id=model_id,
        provider="volcengine-ark",
        per_image=per_image,
        currency="CNY",
        raw={"company": "ByteDance / Doubao-Seed"},
    )


class ImageFxTest(unittest.TestCase):
    def setUp(self):
        # ECB 发布的是「1 欧元兑 X」，USD 也在其中；usd_per() 用两者相除得出
        # 1 CNY 值多少 USD。这里不硬编码换算结果，一律从 snapshot 推导，
        # 免得测试变成在校验我自己抄的数字。
        self.snapshot = FxSnapshot(
            as_of="2026-08-26",
            fetched_at="2026-08-26T00:00:00+00:00",
            rates_per_eur={"USD": 1.16, "CNY": 7.79575},
        )
        self.cny_to_usd = self.snapshot.usd_per("CNY")

    def test_per_image_is_converted_to_usd(self):
        """按张价必须和 token 价一样参与换算——它在 PRICE_FIELDS 里。"""
        record = _cny_image_record("doubao-seedream-5-0-pro", 0.259)
        converted, unsupported = convert_records_to_usd([record], self.snapshot)
        self.assertEqual(converted, 1)
        self.assertEqual(unsupported, set())
        self.assertEqual(record.currency, "USD")
        self.assertAlmostEqual(record.per_image, 0.259 * self.cny_to_usd, places=9)

    def test_converted_per_image_keeps_fx_provenance(self):
        """换算后要留下 ⇄ 标记、原币金额、汇率与 ECB 出处。"""
        record = _cny_image_record("doubao-seedream-5-0-lite", 0.2)
        convert_records_to_usd([record], self.snapshot)
        marker, note, source_url = fx_cells(record, "per_image")
        self.assertTrue(marker, "缺少 ⇄ 标记，读者看不出这是换算值")
        self.assertIn("CNY 0.2", note)
        self.assertIn("ECB 2026-08-26", note)
        self.assertIn("ecb.europa.eu", source_url)

    def test_usd_record_has_no_fx_cells(self):
        """美元原生报价不应凭空出现换算标记。"""
        record = _cny_image_record("gpt-image-1", 0.011)
        record.currency = "USD"
        self.assertEqual(fx_cells(record, "per_image"), ["", "", ""])

    def test_image_group_declares_fx_columns(self):
        """image / audio / video 三组都要有 fx 三列，否则换算值无处溯源。"""
        for prefix in ("image_official", "image_cheapest",
                       "audio_official", "audio_cheapest_input",
                       "video_official", "video_cheapest"):
            for suffix in ("fx_marker", "fx_note", "fx_source_url"):
                self.assertIn(f"{prefix}_{suffix}", COLUMNS)

    def test_fx_columns_land_in_the_per_image_table(self):
        """端到端：人民币按张价进导出层后，fx 列真的被写出来。"""
        raw_models = [r for r in load_raw(ROOT / "raw.csv")
                      if r.model == "Doubao Seedream 5.0 pro"]
        self.assertTrue(raw_models, "raw.csv 里应有 Doubao Seedream 5.0 pro")
        record = _cny_image_record("Doubao Seedream 5.0 pro", 0.259)
        convert_records_to_usd([record], self.snapshot)
        out = ROOT / "tests" / "_tmp_fx_image.csv"
        try:
            write_table(
                out, raw_models, {raw_models[0].model: record},
                {raw_models[0].model: [record]}, {}, {},
                export_functions={"Image Generation"}, price_mode="per_image",
            )
            text = out.read_text(encoding="utf-8")
            header = text.splitlines()[0].split(",")
            self.assertIn("image_official_fx_marker", header)
            self.assertIn("⇄", text)
            self.assertIn("CNY 0.259", text)
        finally:
            out.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
