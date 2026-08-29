import unittest

from src.adapters.price_apis import parse_api


class PriceApiParserTests(unittest.TestCase):
    def test_nested_video_duration_prices_keep_qualifiers(self):
        payload = {
            "data": [{
                "id": "vendor/video-model",
                "pricing": {
                    "video_duration_pricing": [
                        {"resolution": "720p", "audio": False, "cost_per_second": "0.10"},
                        {"resolution": "1080p", "audio": True, "cost_per_second": "0.25"},
                    ]
                },
            }]
        }
        spec = {
            "id": "gateway",
            "name": "Gateway",
            "url": "https://example.test/models",
            "weblink": "https://example.test",
            "list_path": "data",
            "id_fields": ["id"],
            "unit": "per_token",
            "tier": 3,
            "nested_list": "pricing.video_duration_pricing",
            "qualifier_fields": ["resolution", "audio"],
            "fields": {"per_second": "cost_per_second"},
        }
        records, warnings, _free = parse_api(
            payload,
            spec,
            source_url=spec["url"],
            fetched_at="2026-08-27T00:00:00+00:00",
            source_version=None,
        )
        self.assertEqual([], warnings)
        self.assertEqual([0.10, 0.25], [r.per_second for r in records])
        self.assertEqual(
            ["resolution=720p, audio=False", "resolution=1080p, audio=True"],
            [r.qualifier for r in records],
        )

    def test_flat_frame_price_is_preserved_without_fps_conversion(self):
        spec = {
            "id": "frame_api",
            "name": "Frame API",
            "id_fields": ["id"],
            "unit": "per_1m",
            "flat_unit": "cents",
            "tier": 3,
            "fields": {"per_frame": "pricing.cents_per_frame_unit"},
        }
        records, warnings, _free = parse_api(
            [{"id": "video-model", "pricing": {"cents_per_frame_unit": 0.2}}],
            spec,
            source_url="https://example.com/models",
            fetched_at="2026-08-28T00:00:00Z",
            source_version=None,
        )
        self.assertEqual(warnings, [])
        self.assertEqual(len(records), 1)
        self.assertAlmostEqual(records[0].per_frame, 0.002)
        self.assertIsNone(records[0].per_second)


if __name__ == "__main__":
    unittest.main()
