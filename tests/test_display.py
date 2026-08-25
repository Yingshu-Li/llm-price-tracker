import unittest

from src.display import display_name


class DisplayNameTests(unittest.TestCase):
    def test_decimal_parameter_sizes_are_preserved(self):
        cases = {
            "Qwen/Qwen2.5-0.5B-Instruct": "Qwen2.5 0.5B Instruct",
            "Qwen/Qwen2.5-1.5B-Instruct": "Qwen2.5 1.5B Instruct",
            "Qwen/Qwen3-0.6B": "Qwen3 0.6B",
            "Qwen/Qwen3-1.7B": "Qwen3 1.7B",
            "DeepSeek-R1-Distill-Qwen-1.5B": "DeepSeek R1 Distill Qwen 1.5B",
            "tencent/Hunyuan-0.5B-Instruct": "Hunyuan 0.5B Instruct",
            "tencent/Hunyuan-1.8B-Instruct": "Hunyuan 1.8B Instruct",
            "upstage/SOLAR-10.7B-Instruct-v1.0": "SOLAR 10.7B Instruct V1.0",
            "tiiuae/Falcon-H1-0.5B-Instruct": "Falcon H1 0.5B Instruct",
            "tiiuae/Falcon-H1-1.5B-Instruct": "Falcon H1 1.5B Instruct",
        }
        for model, expected in cases.items():
            with self.subTest(model=model):
                self.assertEqual(display_name(model), expected)

    def test_decimal_versions_still_keep_their_dots(self):
        self.assertEqual(display_name("gpt-5.1-mini"), "GPT-5.1 Mini")
        self.assertEqual(display_name("MiniMax-M2.7"), "MiniMax M2.7")
        self.assertEqual(display_name("model.v1.0"), "Model V1.0")


if __name__ == "__main__":
    unittest.main()
