import unittest
from pathlib import Path

from src.modalities import (
    ModalityRecord,
    parse_litellm_modalities,
    parse_modelsdev_modalities,
    load_manual_modalities,
    select_capability,
)
from src.normalize import load_raw


ROOT = Path(__file__).resolve().parents[1]


class InputModalityTests(unittest.TestCase):
    def test_modelsdev_reads_input_modalities_without_requiring_a_price(self):
        records = parse_modelsdev_modalities(
            {
                "google": {
                    "models": {
                        "gemini-2.5-pro": {
                            "name": "Gemini 2.5 Pro",
                            "modalities": {
                                "input": ["text", "image", "audio", "video", "pdf"],
                                "output": ["text"],
                            },
                        }
                    }
                }
            }
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(
            records[0].input_modalities,
            ("text", "image", "audio", "video", "pdf"),
        )
        self.assertEqual(records[0].priority, 10)

    def test_litellm_combines_explicit_capability_flags(self):
        records = parse_litellm_modalities(
            {
                "qwen/qwen-vl": {
                    "mode": "embedding",
                    "litellm_provider": "qwen",
                    "supports_embedding_image_input": True,
                    "supports_video_input": True,
                }
            }
        )

        self.assertEqual(records[0].input_modalities, ("text", "image", "video"))

    def test_first_party_capability_beats_hosted_pdf_wrapper(self):
        def record(source, modalities, priority, url):
            return ModalityRecord(
                source=source,
                model_id="model",
                input_modalities=modalities,
                source_url=url,
                priority=priority,
                raw={"company": "OpenAI"},
            )

        capability = select_capability(
            [
                record("modelsdev", ("text", "image"), 10, "https://official"),
                record(
                    "modelsdev",
                    ("text", "image", "pdf"),
                    30,
                    "https://hosted",
                ),
            ]
        )

        self.assertEqual(capability.modalities_cell, "text | image")
        self.assertEqual(capability.source_urls_cell, "https://official")

    def test_image_generation_overrides_distinguish_generation_and_editing(self):
        raw = load_raw(ROOT / "raw.csv")
        records = load_manual_modalities(
            ROOT / "config" / "input_modalities.yaml", raw
        )
        by_id = {record.model_id: record for record in records if record.priority == 0}

        self.assertEqual(
            by_id["meituan-longcat/LongCat-Image"].input_modalities,
            ("text",),
        )
        self.assertEqual(
            by_id["meituan-longcat/LongCat-Image-Edit"].input_modalities,
            ("text", "image"),
        )
        self.assertEqual(
            by_id["amazon.nova-canvas-v1:0"].input_modalities,
            ("text", "image"),
        )


if __name__ == "__main__":
    unittest.main()
