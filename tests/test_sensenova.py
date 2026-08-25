import unittest
from pathlib import Path

from src.adapters.sensenova import (
    parse_model_list_prices,
    parse_prices,
    parse_token_plan,
)
from src.fx import FxSnapshot, convert_records_to_usd
from src.match import load_aliases, match_all
from src.normalize import load_raw


PRICE_HTML = """
<table>
  <tr><td>SenseNova-V6.5-Pro模型调用</td><td>输入tokens</td><td>0.003元/千tokens</td><td>输出tokens</td><td>0.009元/千tokens</td></tr>
  <tr><td>SenseNova-V6.5-Turbo模型调用</td><td>输入tokens</td><td>0.0015元/千tokens</td><td>输出tokens</td><td>0.0045元/千tokens</td></tr>
  <tr><td>SenseNova-V6-Pro模型调用</td><td>输入tokens</td><td>0.003元/千tokens</td><td>输出tokens</td><td>0.009元/千tokens</td></tr>
  <tr><td>SenseNova-V6-Turbo模型调用</td><td>输入tokens</td><td>0.0015元/千tokens</td><td>输出tokens</td><td>0.0045元/千tokens</td></tr>
  <tr><td>SenseNova-V6-Reasoner模型调用</td><td>输入tokens</td><td>0.004元/千tokens</td><td>输出tokens</td><td>0.016元/千tokens</td></tr>
  <tr><td>SenseNova-V6-Omni模型调用</td><td>分钟</td><td>0.2元/分钟</td></tr>
  <tr><td>SenseChat-Vision模型调用</td><td>输入tokens</td><td>0.01元/千tokens</td><td>输出tokens</td><td>0.06元/千tokens</td></tr>
  <tr><td>SenseChat-Character-Pro模型调用</td><td>输入tokens、输出tokens</td><td>0.015元/千tokens</td></tr>
  <tr><td>SenseChat-Character模型调用</td><td>输入tokens、输出tokens</td><td>0.012元/千tokens</td></tr>
</table>
"""

MODEL_LIST_HTML = """
<table>
  <tr><td>SenseChat-5</td><td>128K</td><td>0.008元/千tokens</td><td>0.02元/千tokens</td></tr>
  <tr><td>SenseChat</td><td>4K</td><td>0.012元/千tokens</td><td>0.012元/千tokens</td></tr>
  <tr><td>SenseChat-Turbo</td><td>快速问答</td><td>0.0003元/千tokens</td><td>0.0006元/千tokens</td></tr>
  <tr><td>SenseChat-5-Cantonese</td><td>32K</td><td>0.027元/千tokens</td><td>0.027元/千tokens</td></tr>
</table>
"""


class SenseNovaParserTests(unittest.TestCase):
    def test_parses_current_pricing_page(self):
        records, warnings = parse_prices(
            PRICE_HTML,
            source_url="https://example.test/pricing",
            fetched_at="2026-08-24T00:00:00+00:00",
            source_version=None,
        )
        self.assertEqual([], warnings)
        self.assertEqual(9, len(records))
        by_model = {record.model_id: record for record in records}
        self.assertEqual((3.0, 9.0, "CNY"), (
            by_model["SenseNova-V6.5-Pro"].input_per_1m,
            by_model["SenseNova-V6.5-Pro"].output_per_1m,
            by_model["SenseNova-V6.5-Pro"].currency,
        ))
        self.assertEqual((4.0, 16.0), (
            by_model["SenseNova-V6-Reasoner"].input_per_1m,
            by_model["SenseNova-V6-Reasoner"].output_per_1m,
        ))
        self.assertEqual((15.0, 15.0), (
            by_model["SenseChat-Character-Pro"].input_per_1m,
            by_model["SenseChat-Character-Pro"].output_per_1m,
        ))
        self.assertAlmostEqual(0.2 / 60, by_model["SenseNova-V6-Omni"].per_second)

    def test_parses_general_model_list_without_prefix_collision(self):
        records, warnings = parse_model_list_prices(
            MODEL_LIST_HTML,
            source_url="https://example.test/models",
            fetched_at="2026-08-24T00:00:00+00:00",
            source_version=None,
        )
        self.assertEqual([], warnings)
        by_model = {record.model_id: record for record in records}
        self.assertEqual(4, len(by_model))
        self.assertEqual((8.0, 20.0), (
            by_model["SenseChat-5"].input_per_1m,
            by_model["SenseChat-5"].output_per_1m,
        ))
        self.assertEqual((27.0, 27.0), (
            by_model["SenseChat-5-Cantonese"].input_per_1m,
            by_model["SenseChat-5-Cantonese"].output_per_1m,
        ))

    def test_token_plan_is_limited_free_not_zero_price(self):
        free, warnings = parse_token_plan(
            """
            <h2>公测期完全免费开放</h2><div>¥ 0/月</div>
            <p>每模型 1,500 次调用 / 5 小时</p>
            <li>SenseNova 6.8 Flash-Lite 与 SenseNova U1 Fast</li>
            """,
            source_url="https://example.test/token-plan",
        )
        self.assertEqual([], warnings)
        self.assertEqual(
            {"SenseNova 6.8 Flash Lite", "SenseNova U1 Fast"}, set(free)
        )

    def test_matches_raw_models_and_converts_cny(self):
        first, first_warnings = parse_prices(
            PRICE_HTML,
            source_url="https://example.test/pricing",
            fetched_at="2026-08-24T00:00:00+00:00",
            source_version=None,
        )
        second, second_warnings = parse_model_list_prices(
            MODEL_LIST_HTML,
            source_url="https://example.test/models",
            fetched_at="2026-08-24T00:00:00+00:00",
            source_version=None,
        )
        self.assertEqual([], first_warnings + second_warnings)
        records = first + second
        for record in records:
            record.raw["company"] = "SenseTime"

        root = Path(__file__).resolve().parents[1]
        expected = {record.model_id for record in records}
        raw_models = [
            row for row in load_raw(root / "raw.csv") if row.model in expected
        ]
        report = match_all(
            raw_models,
            records,
            load_aliases(root / "config" / "aliases.yaml"),
        )
        self.assertEqual(expected, report.matched_models)

        converted, unsupported = convert_records_to_usd(
            records,
            FxSnapshot(
                as_of="2026-08-24",
                fetched_at="2026-08-24T00:00:00+00:00",
                rates_per_eur={"USD": 1.2, "CNY": 8.0},
            ),
        )
        self.assertEqual(13, converted)
        self.assertEqual(set(), unsupported)
        self.assertAlmostEqual(0.45, records[0].input_per_1m)
        self.assertTrue(all(record.raw.get("fx_marker") == "⇄" for record in records))


if __name__ == "__main__":
    unittest.main()
