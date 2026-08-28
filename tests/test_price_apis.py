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


if __name__ == "__main__":
    unittest.main()
