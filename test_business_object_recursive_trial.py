"""Recorded trial adapter tests; no network or credentials required."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from experiments.business_object_trial.run_recursive import RecordedModel


def response(text, stop_reason="end_turn"):
    return SimpleNamespace(
        usage=SimpleNamespace(input_tokens=10, output_tokens=20),
        stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)],
    )


class RecordedAdapterTests(unittest.TestCase):
    def test_preserves_raw_response_and_normalization_without_hidden_retry(self):
        with TemporaryDirectory() as directory:
            client = Mock()
            client.messages.create.return_value = response('```json\n{"facts": []}\n```')
            adapter = RecordedModel(client, "explicit-model", Path(directory))
            self.assertEqual(adapter(system="System", user="Input"), {"facts": []})
            client.messages.create.assert_called_once()
            call = Path(directory) / "call_01"
            self.assertEqual((call / "user.txt").read_text(), "Input")
            self.assertEqual((call / "response.txt").read_text(), '```json\n{"facts": []}\n```')
            record = json.loads((call / "run.json").read_text())
            self.assertEqual(record["format_normalization"], "removed_complete_json_code_fence")
            self.assertEqual(record["usage"], {"input_tokens": 10, "output_tokens": 20})

    def test_incomplete_response_and_nondictionary_are_rejected_and_recorded(self):
        for raw, stop_reason in (("{}", "max_tokens"), ("[]", "end_turn")):
            with self.subTest(raw=raw), TemporaryDirectory() as directory:
                client = Mock()
                client.messages.create.return_value = response(raw, stop_reason)
                adapter = RecordedModel(client, "explicit-model", Path(directory))
                with self.assertRaises(ValueError):
                    adapter(system="System", user="Input")
                self.assertEqual(adapter.calls[0]["status"], "failed")
                client.messages.create.assert_called_once()

    def test_provider_error_records_type_without_error_text(self):
        with TemporaryDirectory() as directory:
            client = Mock()
            client.messages.create.side_effect = RuntimeError("secret-token")
            adapter = RecordedModel(client, "explicit-model", Path(directory))
            with self.assertRaises(RuntimeError):
                adapter(system="System", user="Input")
            record = (Path(directory) / "call_01" / "run.json").read_text()
            self.assertNotIn("secret-token", record)
            self.assertEqual(json.loads(record)["error_type"], "RuntimeError")
            client.messages.create.assert_called_once()


if __name__ == "__main__":
    unittest.main()
