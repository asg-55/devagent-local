import unittest

from devagent.builder import extract_content, extract_json


class BuilderParsingTests(unittest.TestCase):
    def test_extracts_json_from_fence(self):
        result = extract_json('```json\n{"summary":"ok","files":[]}\n```')
        self.assertEqual(result["summary"], "ok")

    def test_extracts_wrapped_file_content(self):
        result = extract_content("before <<<CONTENT>>>\nhello\n<<<END>>> after")
        self.assertEqual(result, "hello")


if __name__ == "__main__":
    unittest.main()

