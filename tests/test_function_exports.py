import csv
import tempfile
import unittest
from pathlib import Path

from src.export import write_table
from src.normalize import load_raw
from src.records import PriceRecord, TIER_OFFICIAL


ROOT = Path(__file__).resolve().parents[1]


def _record(model_id: str, **prices) -> PriceRecord:
    return PriceRecord(
        source="test_official",
        source_tier=TIER_OFFICIAL,
        is_official=True,
        source_url="https://example.com/pricing",
        fetched_at="2026-08-24T00:00:00Z",
        source_snippet=f"{model_id} test price",
        unit_original="test unit",
        model_id=model_id,
        provider="Test Provider",
        **prices,
    )


class FunctionExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw_models = load_raw(ROOT / "raw.csv")

    def _export(
        self, function: str, records_by_model, *, include_text_output_prices=True
    ):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.csv"
            write_table(
                path,
                self.raw_models,
                {},
                records_by_model,
                export_functions={function},
                token_prices_only=True,
                include_text_output_prices=include_text_output_prices,
            )
            with path.open(encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                return reader.fieldnames, list(reader)

    def test_coding_keeps_all_models_but_ignores_non_token_prices(self):
        coding = [row for row in self.raw_models if row.function == "Coding"]
        token_model, non_token_model = coding[:2]
        fields, rows = self._export(
            "Coding",
            {
                token_model.model: [
                    _record(token_model.model, input_per_1m=1.25, output_per_1m=5.0)
                ],
                non_token_model.model: [_record(non_token_model.model, per_call=0.2)],
            },
        )

        self.assertEqual({row["Function"] for row in rows}, {"Coding"})
        self.assertEqual(len(rows), len(coding))
        by_model = {row["Model"]: row for row in rows}
        self.assertEqual(by_model[token_model.model]["price_status"], "got")
        self.assertEqual(
            by_model[token_model.model]["text_cheapest_input_per_1m_usd"], "1.25"
        )
        self.assertNotEqual(by_model[non_token_model.model]["price_status"], "got")
        self.assertEqual(
            by_model[non_token_model.model]["text_cheapest_input_per_1m_usd"], ""
        )
        self.assertNotIn("image_official_per_image_usd", fields)
        self.assertNotIn("video_official_per_second_usd", fields)

    def test_embedding_output_contains_only_embedding_models(self):
        embedding = [row for row in self.raw_models if row.function == "Embedding"]
        model = embedding[0]
        _, rows = self._export(
            "Embedding", {model.model: [_record(model.model, input_per_1m=0.02)]}
        )

        self.assertEqual({row["Function"] for row in rows}, {"Embedding"})
        self.assertEqual(len(rows), len(embedding) - 1)
        by_model = {row["Model"]: row for row in rows}
        self.assertEqual(by_model[model.model]["price_status"], "got")
        self.assertEqual(
            by_model[model.model]["text_official_input_per_1m_usd"], "0.02"
        )
        self.assertNotIn("rerank-v4.0", by_model)

    def test_embedding_drops_generic_output_prices_and_sources(self):
        embedding = [
            row
            for row in self.raw_models
            if row.function == "Embedding" and row.model != "rerank-v4.0"
        ]
        model = embedding[0]
        fields, rows = self._export(
            "Embedding",
            {
                model.model: [
                    _record(model.model, input_per_1m=0.02, output_per_1m=0.10)
                ]
            },
            include_text_output_prices=False,
        )

        row = next(item for item in rows if item["Model"] == model.model)
        self.assertEqual(row["text_official_input_per_1m_usd"], "0.02")
        self.assertEqual(row["text_cheapest_input_per_1m_usd"], "0.02")
        self.assertNotIn("text_official_output_per_1m_usd", fields)
        self.assertNotIn("text_cheapest_output_per_1m_usd", fields)
        self.assertNotIn("text_cheapest_output_source_url", fields)


if __name__ == "__main__":
    unittest.main()
