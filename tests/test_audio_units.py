"""音频五张表与时长归一化。

锁住三件最容易悄悄坏掉的事：
1. 时/分/秒归一到分钟，且换算过的必须带 ⏱ 标记（没标记 = 看不出被换算过）
2. 计量对象不同的价格**不合并**（合并会产出假的「最低价」）
3. DeepInfra 的 cents_per_sec 是 GPU 计算秒，绝不能当音频时长
"""
import csv
import tempfile
import unittest
from pathlib import Path

from src.export import (
    AUDIO_UNIT_COLUMNS,
    PRICE_MODE_AUDIO_CHAR,
    PRICE_MODE_AUDIO_TIME,
    write_audio_unit_table,
)
from src.adapters.deepinfra_audio import parse_deepinfra_audio
from src.normalize import load_raw
from src.records import PriceRecord, TIER_OFFICIAL, TIER_THIRDPARTY
from src.units import TIME_MARKER, to_per_minute

ROOT = Path(__file__).resolve().parents[1]


class TimeNormalizationTests(unittest.TestCase):
    def test_hour_and_second_convert_to_minute(self):
        self.assertAlmostEqual(to_per_minute(0.22, "hour")[0], 0.22 / 60)
        self.assertAlmostEqual(to_per_minute(0.0001, "second")[0], 0.006)

    def test_already_per_minute_gets_no_marker(self):
        """没换算就不该有「已换算」的提示，否则标记会失去含义。"""
        value, patch = to_per_minute(0.006, "minute")
        self.assertEqual(value, 0.006)
        self.assertEqual(patch, {})

    def test_converted_value_carries_marker_and_original(self):
        _, patch = to_per_minute(0.22, "hour")
        self.assertEqual(patch["time_marker"], TIME_MARKER)
        self.assertEqual(patch["time_original_unit"], "hour")
        self.assertIn("0.22", patch["time_note"])

    def test_unknown_unit_raises_instead_of_guessing(self):
        with self.assertRaises(ValueError):
            to_per_minute(1.0, "day")


def _rec(model, *, basis, per_minute=None, per_1m_chars=None, official=False,
         provider="X", src="t_audio"):
    return PriceRecord(
        source=src,
        source_tier=TIER_OFFICIAL if official else TIER_THIRDPARTY,
        is_official=official,
        source_url="https://example.com/audio-pricing",
        fetched_at="2026-08-30T00:00:00Z",
        source_snippet=f"{model} audio price",
        unit_original="per minute of audio",
        model_id=model, provider=provider, modality="audio",
        billing_basis=basis, per_minute=per_minute, per_1m_chars=per_1m_chars,
    )


class AudioUnitTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw_models = load_raw(ROOT / "raw.csv")
        cls.model = next(
            m.model for m in cls.raw_models if m.function == "Speech & Audio"
        )

    def _write(self, records, mode):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.csv"
            write_audio_unit_table(
                path, self.raw_models, {self.model: records}, price_mode=mode,
            )
            with path.open(encoding="utf-8", newline="") as fh:
                return list(csv.DictReader(fh))

    def test_different_billing_basis_never_merge(self):
        """输入音频 $0.10 与产出音频 $0.01 必须分两行。

        合并的话 cheapest 会取 $0.01，等于用 TTS 的产出价冒充 ASR 的输入价。
        """
        rows = self._write([
            _rec(self.model, basis="input_audio", per_minute=0.10),
            _rec(self.model, basis="output_audio", per_minute=0.01, provider="Y"),
        ], PRICE_MODE_AUDIO_TIME)
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["billing_basis"] for r in rows},
                         {"input_audio", "output_audio"})
        for r in rows:
            price = float(r["audio_unit_cheapest_price_usd"])
            self.assertEqual(price, 0.10 if r["billing_basis"] == "input_audio" else 0.01)

    def test_billing_basis_column_never_dropped(self):
        """这一列缺失会让不同计量对象的价格看起来可以直接比较。"""
        rows = self._write(
            [_rec(self.model, basis=None, per_minute=0.05)], PRICE_MODE_AUDIO_TIME)
        self.assertIn("billing_basis", rows[0])

    def test_csv_stores_language_neutral_key(self):
        """CSV 存 key 不存中文，否则英文界面这一列会显示中文。"""
        rows = self._write(
            [_rec(self.model, basis="session", per_minute=0.3)], PRICE_MODE_AUDIO_TIME)
        self.assertEqual(rows[0]["billing_basis"], "session")

    def test_char_table_is_separate_unit(self):
        rows = self._write(
            [_rec(self.model, basis=None, per_1m_chars=15.0)], PRICE_MODE_AUDIO_CHAR)
        self.assertEqual(float(rows[0]["audio_unit_cheapest_price_usd"]), 15.0)
        self.assertEqual(rows[0]["billing_unit"], "per_1m_chars")

    def test_row_length_matches_column_spec(self):
        rows = self._write(
            [_rec(self.model, basis="input_audio", per_minute=0.02)],
            PRICE_MODE_AUDIO_TIME)
        self.assertTrue(set(rows[0]).issubset(set(AUDIO_UNIT_COLUMNS)))


class DeepInfraAudioTests(unittest.TestCase):
    """cents_per_sec 是 GPU 计算秒，不是音频时长——绝不能映射。"""

    PAYLOAD = """[
      {"model_name": "openai/whisper-x", "type": "automatic-speech-recognition",
       "pricing": {"cents_per_sec": 0.05}},
      {"model_name": "canopylabs/orpheus-x", "type": "text-to-speech",
       "pricing": {"cents_per_input_chars": 0.0007}},
      {"model_name": "Qwen/Qwen3-ASR-x", "type": "automatic-speech-recognition",
       "pricing": {"cents_per_input_sec": 0.005}}
    ]"""

    def setUp(self):
        self.records, _ = parse_deepinfra_audio(
            self.PAYLOAD, source_url="https://deepinfra.com/models",
            fetched_at="2026-08-30T00:00:00Z", source_version=None)
        self.by_id = {r.model_id: r for r in self.records}

    def test_cents_per_sec_is_never_read(self):
        """该字段同时出现在 CLIP 与 Stable Diffusion 上，是计算秒不是音频秒。"""
        self.assertNotIn("openai/whisper-x", self.by_id)

    def test_input_sec_anchors_to_official_per_minute(self):
        """锚点：DeepInfra 官方页对 Voxtral-Small 印的是 $0.00300 per minute。"""
        rec = self.by_id["Qwen/Qwen3-ASR-x"]
        self.assertAlmostEqual(rec.per_minute, 0.003)
        self.assertEqual(rec.billing_basis, "input_audio")
        self.assertEqual(rec.raw["time_marker"], TIME_MARKER)

    def test_chars_normalize_to_per_1m(self):
        rec = self.by_id["canopylabs/orpheus-x"]
        self.assertAlmostEqual(rec.per_1m_chars, 7.0)

    def test_company_key_is_named_company(self):
        """match._index_by_company 只认 raw["company"]，写错整个源静默 0 匹配。"""
        for rec in self.records:
            self.assertTrue(rec.raw.get("company"))



class GoogleAudioTests(unittest.TestCase):
    """Lyria 的 per song 是唯一没有别的源覆盖的音频单位，解析必须稳。"""

    PAGE = (
        "<td>Lyria 3 Clip Preview (30s)</td><td>Not available</td>"
        "<td>$0.04 per song</td>"
        "<td>Lyria 3 Pro Preview (Full Song)</td><td>Not available</td>"
        "<td>$0.08 per song</td>"
    )

    def _parse(self, page):
        from src.adapters.google_audio import parse_google_audio, GOOGLE_PRICING_URL
        return parse_google_audio(
            page, source_url=GOOGLE_PRICING_URL,
            fetched_at="2026-08-30T00:00:00Z", source_version=None)

    def test_parses_both_lyria_rows_as_official_per_call(self):
        records, warnings = self._parse(self.PAGE)
        self.assertEqual(warnings, [])
        by_id = {r.model_id: r for r in records}
        self.assertAlmostEqual(by_id["lyria-3-clip-preview"].per_call, 0.04)
        self.assertAlmostEqual(by_id["lyria-3-pro-preview"].per_call, 0.08)
        for rec in records:
            self.assertTrue(rec.is_official)
            self.assertIsNone(rec.billing_basis)  # 按次与时长无关

    def test_unknown_row_label_warns_instead_of_guessing(self):
        """页面加了新行时必须报警，绝不能模糊匹配到别的模型头上。"""
        records, warnings = self._parse(
            "<td>Lyria 4 Ultra (Something)</td><td>$0.99 per song</td>")
        self.assertEqual(records, [])
        self.assertTrue(any("核验表" in w for w in warnings))

    def test_duplicate_occurrence_is_not_double_counted(self):
        """同一价格在概览与详表里各出现一次，不能变成两条观测。"""
        records, _ = self._parse(self.PAGE + self.PAGE)
        self.assertEqual(len(records), 2)

    def test_empty_page_warns(self):
        records, warnings = self._parse("<html><body>no prices</body></html>")
        self.assertEqual(records, [])
        self.assertTrue(warnings)


class ZhipuAudioTests(unittest.TestCase):
    """智谱一家覆盖四种单位；实时那两条同时印音频价与视频价，不能取错。"""

    def _content(self, rows):
        import json as _j
        return _j.dumps({"data": [{"content": _j.dumps({"list": [{
            "modelName": "语音模型",
            "fieldList": [{"code": "m", "label": "模型"},
                          {"code": "p", "label": "单价"}],
            "modelList": rows,
        }]}, ensure_ascii=False)}]}, ensure_ascii=False)

    def _parse(self, rows):
        from src.adapters.zhipu_audio import parse_zhipu_audio, ZHIPU_WEBLINK
        return parse_zhipu_audio(
            self._content(rows), source_url=ZHIPU_WEBLINK,
            fetched_at="2026-08-30T00:00:00Z", source_version=None)

    def test_realtime_takes_audio_price_not_video(self):
        """音频 0.18 与视频 1.2 差 6.7 倍，取错不会被数值本身看出来。"""
        recs, warns = self._parse([
            {"m": "GLM-Realtime-Flash", "p": "音频：0.18元/分钟；视频：1.2元/分钟"}])
        self.assertEqual(warns, [])
        self.assertAlmostEqual(recs[0].per_minute, 0.18)
        self.assertEqual(recs[0].currency, "CNY")

    def test_four_units_all_land_on_right_field(self):
        recs, _ = self._parse([
            {"m": "GLM-TTS", "p": "2元/万字符"},
            {"m": "GLM-TTS-Clone", "p": "6元/次"},
            {"m": "GLM-4-Voice", "p": "80 元 / 百万Tokens"},
            {"m": "GLM-Realtime-Air", "p": "音频：0.3元/分钟；视频：2.1元/分钟"},
        ])
        by = {r.model_id: r for r in recs}
        self.assertAlmostEqual(by["GLM-TTS"].per_1m_chars, 200)   # 万 -> 百万
        self.assertAlmostEqual(by["GLM-TTS-Clone"].per_call, 6)
        self.assertAlmostEqual(by["GLM-4-Voice"].input_per_1m, 80)
        self.assertAlmostEqual(by["GLM-Realtime-Air"].per_minute, 0.3)

    def test_token_price_has_no_audio_modality(self):
        """标成 audio 会让它从按 token 表里消失（该表读 text_official_* 列）。"""
        recs, _ = self._parse([{"m": "GLM-ASR-2512", "p": "输入：16元/百万 tokens"}])
        self.assertIsNone(recs[0].modality)

    def test_unverified_model_is_skipped_with_warning(self):
        recs, warns = self._parse([{"m": "GLM-TTS-Ultra", "p": "9元/万字符"}])
        self.assertEqual(recs, [])
        self.assertTrue(any("未核验" in w for w in warns))

    def test_field_codes_are_resolved_via_fieldList(self):
        """字段码是混淆的且逐区块不同，硬编码必然在改版时静默失效。"""
        import json as _j
        payload = _j.dumps({"data": [{"content": _j.dumps({"list": [{
            "modelName": "语音模型",
            "fieldList": [{"code": "zZz9", "label": "模型"},
                          {"code": "qQq1", "label": "单价"}],
            "modelList": [{"zZz9": "GLM-TTS", "qQq1": "2元/万字符"}],
        }]}, ensure_ascii=False)}]}, ensure_ascii=False)
        from src.adapters.zhipu_audio import parse_zhipu_audio, ZHIPU_WEBLINK
        recs, _ = parse_zhipu_audio(
            payload, source_url=ZHIPU_WEBLINK,
            fetched_at="2026-08-30T00:00:00Z", source_version=None)
        self.assertAlmostEqual(recs[0].per_1m_chars, 200)


class MiniMaxAudioTests(unittest.TestCase):
    PAGE = ("<th>单价<br>元/万字符</th>"
            "<td>speech-2.8-hd</td><td>desc</td><td>3.5</td>"
            "<td>speech-2.8-turbo</td><td>desc</td><td>2</td>")

    def _parse(self, page):
        from src.adapters.minimax_audio import parse_minimax_audio, MINIMAX_URL
        return parse_minimax_audio(
            page, source_url=MINIMAX_URL,
            fetched_at="2026-08-30T00:00:00Z", source_version=None)

    def test_wan_chars_scales_to_per_1m(self):
        recs, warns = self._parse(self.PAGE)
        self.assertEqual(warns, [])
        by = {r.model_id: r for r in recs}
        self.assertAlmostEqual(by["speech-2.8-hd"].per_1m_chars, 350)
        self.assertAlmostEqual(by["speech-2.8-turbo"].per_1m_chars, 200)

    def test_unit_header_change_aborts_instead_of_guessing(self):
        """改成千字符还照取 3.5 就是 10 倍错误，而 3.5 本身完全正常。"""
        recs, warns = self._parse(self.PAGE.replace("元/万字符", "元/千字符"))
        self.assertEqual(recs, [])
        self.assertTrue(any("表头单位" in w for w in warns))

if __name__ == "__main__":
    unittest.main()
