"""Test NVIDIA classification contracts and fallbacks without network access."""
import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from sectors import SectorClassifier, SectorError, enrich_sectors

TEST_MODEL = "nvidia/nemotron-3.5-bolt-e4b"
TEST_URL = "https://integrate.api.nvidia.com/v1/chat/completions"


def completion(rows, finish_reason="stop"):
    return io.BytesIO(json.dumps({"choices": [{"finish_reason": finish_reason,
        "message": {"content": json.dumps({"sectors": rows})}}]}).encode())


class SectorTests(unittest.TestCase):
    def setUp(self):
        self.config = patch.dict(os.environ, {"NVIDIA_MODEL": TEST_MODEL, "NVIDIA_INVOKE_URL": TEST_URL})
        self.config.start()
        self.addCleanup(self.config.stop)

    def test_placeholder_does_not_call_provider(self):
        for token in ("", "dummy_replace_with_your_nvidia_api_key"):
            with patch.dict(os.environ, {"NVIDIA_API_KEY": token}), patch("sectors.urlopen") as request:
                results, message = SectorClassifier().suggest(["TCS"])
            request.assert_not_called()
            self.assertEqual(results, {})
            self.assertIn("placeholder", message)

    def test_payload_and_strict_output(self):
        with patch("sectors.urlopen", return_value=completion([{"index": 0, "sector": "IT"}])) as request:
            result = SectorClassifier()._request(["TCS"], "test-only-token", TEST_MODEL, TEST_URL)
        self.assertEqual(result, ["IT"])
        sent = request.call_args.args[0]
        self.assertEqual(sent.get_header("Authorization"), "Bearer test-only-token")
        self.assertEqual(sent.full_url, "https://integrate.api.nvidia.com/v1/chat/completions")
        payload = json.loads(sent.data)
        self.assertEqual(payload["model"], "nvidia/nemotron-3.5-bolt-e4b")
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(payload["temperature"], 0)

    def test_invalid_or_incomplete_outputs_are_rejected(self):
        cases = [[], [{"index": 0, "sector": "Delivery"}], [{"index": True, "sector": "IT"}],
                 [{"index": 1, "sector": "IT"}]]
        for rows in cases:
            with self.subTest(rows=rows), patch("sectors.urlopen", return_value=completion(rows)):
                with self.assertRaises(SectorError):
                    SectorClassifier()._request(["TCS"], "test", TEST_MODEL, TEST_URL)
        with patch("sectors.urlopen", return_value=completion([{"index": 0, "sector": "IT"}], "length")):
            with self.assertRaises(SectorError):
                SectorClassifier()._request(["TCS"], "test", TEST_MODEL, TEST_URL)

    @patch.dict(os.environ, {"NVIDIA_API_KEY": "test-key", "NVIDIA_MODEL": TEST_MODEL})
    def test_cache_deduplicates_names_and_uncertainty(self):
        classifier = SectorClassifier()
        with patch.object(classifier, "_request", return_value=["IT", "Unclassified"]) as request:
            result, message = classifier.suggest(["TCS", "tcs", "Unknown"])
            self.assertEqual(result, {"TCS": "IT", "tcs": "IT"})
            self.assertEqual(message, "")
            self.assertEqual(classifier.suggest(["TCS", "Unknown"])[0], {"TCS": "IT"})
            request.assert_called_once()

    @patch.dict(os.environ, {"NVIDIA_API_KEY": "test-key"})
    def test_bounded_batch_and_retry_after_failure(self):
        classifier = SectorClassifier()
        names = [f"Company {index}" for index in range(21)]
        with patch.object(classifier, "_request", return_value=["IT"] * 20) as request:
            result, message = classifier.suggest(names)
            self.assertEqual(len(result), 20)
            self.assertIn("next batch", message)
            self.assertEqual(len(request.call_args.args[0]), 20)
        with patch.object(classifier, "_request", side_effect=SectorError("Retry later")):
            result, message = classifier.suggest(["Another"])
            self.assertEqual(result, {})
            self.assertEqual(message, "Retry later")
        with patch.object(classifier, "_request", return_value=["Banking"]):
            self.assertEqual(classifier.suggest(["Another"])[0], {"Another": "Banking"})

    def test_manual_sectors_take_precedence_and_ai_is_separate(self):
        stocks = [
            {"stockName": "TCS", "sector": "IT"}, {"stockName": "tcs"},
            {"stockName": "Example"},
        ]
        with patch("sectors.classifier.suggest", return_value=({"Example": "Pharmaceuticals"}, "")) as request:
            enriched, message = enrich_sectors(stocks)
        request.assert_called_once_with(["Example"])
        self.assertEqual(enriched[0]["sector"], "IT")
        self.assertEqual(enriched[1]["sector"], "IT")
        self.assertNotIn("sector", enriched[2])
        self.assertEqual(enriched[2]["sectorSuggestion"], "Pharmaceuticals")
        self.assertEqual(message, "")

    def test_auth_error_is_safe(self):
        with patch("sectors.urlopen", side_effect=HTTPError("url", 401, "bad", {}, None)):
            with self.assertRaisesRegex(SectorError, "authorization failed"):
                SectorClassifier()._request(["TCS"], "test", TEST_MODEL, TEST_URL)

    @patch.dict(os.environ, {"NVIDIA_API_KEY": "test-key"})
    def test_missing_and_invalid_configuration_does_not_call_provider(self):
        for changes in ({"NVIDIA_MODEL": ""}, {"NVIDIA_INVOKE_URL": ""},
                        {"NVIDIA_INVOKE_URL": "http://example.com/api"},
                        {"NVIDIA_INVOKE_URL": "https://user:password@example.com/api"}):
            with self.subTest(changes=changes), patch.dict(os.environ, changes), patch("sectors.urlopen") as request:
                result, message = SectorClassifier().suggest(["TCS"])
            request.assert_not_called()
            self.assertEqual(result, {})
            self.assertTrue(message)

    @patch.dict(os.environ, {"NVIDIA_API_KEY": "test-key"})
    def test_environment_settings_are_forwarded_and_cache_is_separate(self):
        classifier = SectorClassifier()
        for endpoint, model in ((TEST_URL, TEST_MODEL), ("https://example.com/api", "another-model")):
            with patch.dict(os.environ, {"NVIDIA_INVOKE_URL": endpoint, "NVIDIA_MODEL": model}), patch.object(classifier, "_request", return_value=["IT"]) as request:
                self.assertEqual(classifier.suggest(["TCS"])[0], {"TCS": "IT"})
                request.assert_called_once_with(["TCS"], "test-key", model, endpoint)


if __name__ == "__main__":
    unittest.main()
