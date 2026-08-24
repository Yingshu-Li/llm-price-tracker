import json
import unittest
from pathlib import Path

from src.adapters.upstage import parse_prices
from src.adapters.vendored import parse_litellm
from src.match import load_aliases, match_all
from src.normalize import load_raw


ROOT = Path(__file__).resolve().parents[1]


class EmbeddingPriceTests(unittest.TestCase):
    def test_upstage_official_embed_price(self):
        html = """
        <section>
          <h3>Embed</h3><p>Embedding model for accurate semantic search and retrieval.</p>
          <p>* End of service on 2026-12-31 (UTC).</p>
          <strong>$0.10 / 1M tokens</strong>
          <h3>Embed 2</h3><strong>$0.02 / 1M tokens</strong>
        </section>
        """
        records, warnings = parse_prices(
            html,
            source_url="https://www.upstage.ai/pricing/api",
            fetched_at="2026-08-24T00:00:00Z",
            source_version="test",
        )
        self.assertEqual(warnings, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].model_id, "Upstage Embed")
        self.assertEqual(records[0].input_per_1m, 0.10)

    def test_litellm_uses_provider_to_identify_bare_cohere_models(self):
        payload = {
            "embed-english-v3.0": {
                "litellm_provider": "cohere",
                "mode": "embedding",
                "input_cost_per_token": 0.0000001,
                "output_cost_per_token": 0.0,
            }
        }
        records = parse_litellm(
            payload,
            source_url="https://example.com/litellm.json",
            fetched_at="2026-08-24T00:00:00Z",
            source_version="test",
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].raw["company"], "Cohere")
        self.assertAlmostEqual(records[0].input_per_1m, 0.1)
        self.assertTrue(records[0].is_official)

    def test_litellm_identifies_oci_cohere_prefixed_models(self):
        payload = {
            "oci/cohere.embed-multilingual-light-v3.0": {
                "litellm_provider": "oci",
                "mode": "embedding",
                "input_cost_per_token": 0.0000001,
                "output_cost_per_token": 0.0,
            }
        }
        records = parse_litellm(
            payload,
            source_url="https://example.com/litellm.json",
            fetched_at="2026-08-24T00:00:00Z",
            source_version="test",
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].raw["company"], "Cohere")
        self.assertAlmostEqual(records[0].input_per_1m, 0.1)
        self.assertFalse(records[0].is_official)

    def test_amazon_nova_embedding_alias_matches_litellm_id(self):
        payload = json.loads((ROOT / "vendor" / "litellm.json").read_text("utf-8"))
        records = parse_litellm(
            {
                "amazon.nova-2-multimodal-embeddings-v1:0": payload[
                    "amazon.nova-2-multimodal-embeddings-v1:0"
                ]
            },
            source_url="https://example.com/litellm.json",
            fetched_at="2026-08-24T00:00:00Z",
            source_version="test",
        )
        raw = [
            model
            for model in load_raw(ROOT / "raw.csv")
            if model.model == "Amazon Nova Multimodal Embeddings"
        ]
        report = match_all(raw, records, load_aliases(ROOT / "config" / "aliases.yaml"))
        self.assertEqual(report.matched_models, {"Amazon Nova Multimodal Embeddings"})


if __name__ == "__main__":
    unittest.main()
