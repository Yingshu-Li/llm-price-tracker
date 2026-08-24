import unittest
from pathlib import Path

from src.adapters.iflytek import parse_prices
from src.fx import FxSnapshot, convert_records_to_usd
from src.match import load_aliases, match_all
from src.normalize import load_raw


class IflytekParserTests(unittest.TestCase):
    def test_parses_only_supported_public_prices(self):
        payload = {
            "code": 0,
            "data": {
                "rows": [
                    {
                        "name": "Spark X2",
                        "serviceId": "xsparkx2",
                        "showPrice": True,
                        "updateTime": "2026-08-14 10:01:30",
                        "price": {"inferencePrice": {
                            "inTokensPrice": 3,
                            "inTokensUnit": "元/百万tokens",
                            "outTokensPrice": 3,
                            "outTokensUnit": "元/百万tokens",
                        }},
                    },
                    {
                        "name": "Spark-X2-Flash",
                        "serviceId": "xsparkx2flash",
                        "showPrice": True,
                        "price": {"inferencePrice": {
                            "inTokensPrice": 1,
                            "inTokensUnit": "元/百万tokens",
                            "outTokensPrice": 2,
                            "outTokensUnit": "元/百万tokens",
                        }},
                    },
                    {
                        "name": "Unrelated",
                        "serviceId": "unrelated",
                        "showPrice": True,
                        "price": {"inferencePrice": {}},
                    },
                ]
            },
        }
        records, warnings = parse_prices(
            payload,
            source_url="https://example.test/prices",
            fetched_at="2026-08-24T00:00:00+00:00",
            source_version=None,
        )
        self.assertEqual([], warnings)
        self.assertEqual(["xsparkx2", "xsparkx2flash"], [r.model_id for r in records])
        self.assertEqual((3.0, 3.0, "CNY"), (
            records[0].input_per_1m,
            records[0].output_per_1m,
            records[0].currency,
        ))
        self.assertEqual((1.0, 2.0), (
            records[1].input_per_1m,
            records[1].output_per_1m,
        ))

    def test_rejects_changed_units(self):
        payload = {
            "code": 0,
            "data": {"rows": [{
                "serviceId": "xsparkx2",
                "showPrice": True,
                "price": {"inferencePrice": {
                    "inTokensPrice": 3,
                    "inTokensUnit": "元/千tokens",
                    "outTokensPrice": 3,
                    "outTokensUnit": "元/千tokens",
                }},
            }]},
        }
        records, warnings = parse_prices(
            payload,
            source_url="https://example.test/prices",
            fetched_at="2026-08-24T00:00:00+00:00",
            source_version=None,
        )
        self.assertEqual([], records)
        self.assertTrue(any("单位发生变化" in warning for warning in warnings))

    def test_matches_three_raw_rows_and_converts_to_usd(self):
        payload = {
            "code": 0,
            "data": {"rows": [
                {
                    "name": "Spark X2",
                    "serviceId": "xsparkx2",
                    "showPrice": True,
                    "price": {"inferencePrice": {
                        "inTokensPrice": 3,
                        "inTokensUnit": "元/百万tokens",
                        "outTokensPrice": 3,
                        "outTokensUnit": "元/百万tokens",
                    }},
                },
                {
                    "name": "Spark-X2-Flash",
                    "serviceId": "xsparkx2flash",
                    "showPrice": True,
                    "price": {"inferencePrice": {
                        "inTokensPrice": 1,
                        "inTokensUnit": "元/百万tokens",
                        "outTokensPrice": 2,
                        "outTokensUnit": "元/百万tokens",
                    }},
                },
            ]},
        }
        records, warnings = parse_prices(
            payload,
            source_url="https://example.test/prices",
            fetched_at="2026-08-24T00:00:00+00:00",
            source_version=None,
        )
        self.assertEqual([], warnings)
        for record in records:
            record.raw["company"] = "iFLYTEK"

        root = Path(__file__).resolve().parents[1]
        raw_models = [
            row for row in load_raw(root / "raw.csv")
            if row.model in {
                "Spark-X2 / model=spark-x",
                "Spark-X2 / xsparkx2",
                "Spark-X2-Flash / xsparkx2flash",
            }
        ]
        report = match_all(
            raw_models,
            records,
            load_aliases(root / "config" / "aliases.yaml"),
        )
        self.assertEqual(3, len(report.matches))
        self.assertEqual({row.model for row in raw_models}, report.matched_models)

        converted, unsupported = convert_records_to_usd(
            records,
            FxSnapshot(
                as_of="2026-08-24",
                fetched_at="2026-08-24T00:00:00+00:00",
                rates_per_eur={"USD": 1.2, "CNY": 8.0},
            ),
        )
        self.assertEqual(2, converted)
        self.assertEqual(set(), unsupported)
        self.assertTrue(all(record.raw.get("fx_marker") == "⇄" for record in records))
        self.assertAlmostEqual(0.45, records[0].input_per_1m)


if __name__ == "__main__":
    unittest.main()
