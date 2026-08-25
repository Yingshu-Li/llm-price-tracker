import tempfile
import unittest
from pathlib import Path

from src.adapters.china_official import (
    load_verified_snapshots,
    parse_baidu,
    parse_stepfun,
    parse_tencent,
)


class ChinaOfficialParserTests(unittest.TestCase):
    def test_stepfun_token_table(self):
        html = """
        <table><tr><th>模型</th><th>计费单位</th><th>输入</th><th>缓存</th><th>输出</th></tr>
        <tr><td><code>step-1o-turbo-vision</code></td><td>1M tokens</td>
        <td>2.5元</td><td>0.5元</td><td>8元</td></tr></table>
        """
        records, warnings = parse_stepfun(
            html, source_url="https://example.test/stepfun",
            fetched_at="2026-08-25T00:00:00+00:00", source_version=None,
        )
        self.assertEqual([], warnings)
        self.assertEqual(1, len(records))
        self.assertEqual((2.5, 0.5, 8.0, "CNY"), (
            records[0].input_per_1m, records[0].cache_read_per_1m,
            records[0].output_per_1m, records[0].currency,
        ))

    def test_baidu_rowspan_and_embedding_tables(self):
        html = """
        <h4 id="按量后付费"></h4><table>
        <tr><td rowspan="2">ERNIE 5.1</td><td rowspan="2">ERNIE-5.1</td>
        <td>输入（输入&lt;=32k）</td><td>0.004</td><td>-</td><td>元/千tokens</td></tr>
        <tr><td>输出（输入&lt;=32k）</td><td>0.018</td><td>-</td></tr></table>
        <h4 id="按量后付费-1"></h4><table></table>
        <h4 id="按量后付费-2"></h4><table></table>
        <h3 id="文本向量"></h3><table>
        <tr><td>Embedding-V1</td><td>推理服务</td><td>输入</td><td>0.0005</td><td>元/千tokens</td></tr>
        </table>
        """
        records, warnings = parse_baidu(
            html, source_url="https://example.test/baidu",
            fetched_at="2026-08-25T00:00:00+00:00", source_version=None,
        )
        self.assertEqual([], warnings)
        by_model = {record.model_id: record for record in records}
        self.assertEqual((4.0, 18.0), (
            by_model["ERNIE-5.1"].input_per_1m,
            by_model["ERNIE-5.1"].output_per_1m,
        ))
        self.assertEqual(0.5, by_model["Embedding-V1"].input_per_1m)
        self.assertIsNone(by_model["Embedding-V1"].output_per_1m)

    def test_tencent_ignores_preview_and_keeps_stable_hy3(self):
        html = """
        <table>
        <tr><td>Hy3 preview<br>（2026-08-31 下线）</td><td>0-16k</td><td>-</td><td>1.2</td><td>4</td><td>0.4</td></tr>
        <tr><td>Hy3</td><td>-</td><td>-</td><td>1</td><td>4</td><td>0.25</td></tr>
        </table>
        """
        records, warnings = parse_tencent(
            html, source_url="https://example.test/tencent",
            fetched_at="2026-08-25T00:00:00+00:00", source_version=None,
        )
        self.assertEqual([], warnings)
        self.assertEqual(["Hy3"], [record.model_id for record in records])
        self.assertEqual((1.0, 4.0, 0.25), (
            records[0].input_per_1m, records[0].output_per_1m,
            records[0].cache_read_per_1m,
        ))

    def test_verified_snapshot_retains_audit_fields(self):
        content = """
sources:
  - id: official_verified
    name: Official verified
    provider: vendor
    url: https://example.test/pricing
    verified_at: "2026-08-25T00:00:00+00:00"
prices:
  - source: official_verified
    model_id: model-a
    currency: CNY
    unit: CNY per 1M tokens
    input_per_1m: 1
    output_per_1m: 2
    snippet: "model-a | 1元 input | 2元 output"
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prices.yaml"
            path.write_text(content, encoding="utf-8")
            records, warnings, fetches = load_verified_snapshots(path)
        self.assertEqual(1, len(records))
        self.assertEqual("https://example.test/pricing", records[0].source_url)
        self.assertEqual("CNY", records[0].currency)
        self.assertEqual(1, fetches[0]["n_records"])
        self.assertTrue(any("人工核验快照" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
