# -*- coding: utf-8 -*-
"""Offline checks for plain-English model-server error messages (2026-10-08).

Two live incidents pin these:

1. 2026-10-07 (fresh VM): the user picked an OpenRouter ":batch" model name;
   every message failed with the cryptic "The model did not return a usable
   reply (OpenAIModelNotFoundError)" - an evening of chasing before the cause
   (the ":batch" suffix) was found. Now: a 404 from the model endpoint is
   explained as a model-name problem with a pointer at Settings, and a
   ":batch" name gets a dedicated hint.
2. The test-connection trap: a ":batch" name IS in OpenRouter's model list,
   so the test cheerfully reported "model found". Now the report carries an
   explicit batch-only warning the moment such a name is found.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import api  # noqa: E402
import llm  # noqa: E402


def _not_found_exc(exc_class=None):
    """A real 404 exception from the model endpoint, built against a minimal
    fake response object. Defaults to openai.NotFoundError; pass a subclass
    (e.g. langchain's OpenAIModelNotFoundError) for the wrapped variants."""
    import openai
    cls = exc_class if exc_class is not None else openai.NotFoundError
    fake_response = type("FakeResponse", (),
                         {"status_code": 404, "headers": {}, "request": object()})()
    return cls(message="model not found", response=fake_response, body=None)


class ModelNotFoundMessageTests(unittest.TestCase):
    def test_not_found_is_explained_as_a_model_name_problem(self):
        with patch.object(api, "getLLMModel", return_value="openai/gpt-5.2"):
            msg = api._llm_error_message(_not_found_exc())
        self.assertIn("no model by the name", msg)
        self.assertIn("Settings", msg)
        self.assertIn("openai/gpt-5.2", msg)
        self.assertNotIn("OpenAIModelNotFoundError", msg)

    def test_batch_name_gets_the_batch_hint(self):
        with patch.object(api, "getLLMModel",
                          return_value="deepseek/deepseek-v4.1-flash:batch"):
            msg = api._llm_error_message(_not_found_exc())
        self.assertIn(":batch", msg)
        self.assertIn("24 h", msg)
        self.assertNotIn("no model by the name", msg)

    def test_langchain_wrapped_404_is_treated_the_same(self):
        # Not re-exported at the package top level in langchain_openai 1.x.
        from langchain_openai.chat_models.base import OpenAIModelNotFoundError
        exc = _not_found_exc(OpenAIModelNotFoundError)
        with patch.object(api, "getLLMModel", return_value="x/y:batch"):
            msg = api._llm_error_message(exc)
        self.assertIn(":batch", msg)

    def test_other_failures_keep_the_generic_message(self):
        msg = api._llm_error_message(ValueError("boom"))
        self.assertIn("ValueError", msg)
        self.assertIn("Please try again", msg)


class TestConnectionBatchWarningTests(unittest.TestCase):
    def _probe(self, reachable=True, models=(), error=None):
        return {"reachable": reachable, "models": list(models), "error": error}

    def test_batch_model_found_still_warns(self):
        probe = self._probe(models=["deepseek/deepseek-v4.1-flash:batch"])
        with patch.object(llm, "test_server", return_value=probe), \
             patch.object(api, "getLLMModel",
                          return_value="deepseek/deepseek-v4.1-flash:batch"), \
             patch.object(api.memory, "load_llm_server", return_value=""):
            client = api.application.test_client()
            response = client.get("/testConnection")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["model_found"])  # the trap: the name IS listed
        self.assertIsNotNone(body["batch_warning"])
        self.assertIn(":batch", body["batch_warning"])

    def test_regular_model_has_no_warning(self):
        probe = self._probe(models=["deepseek/deepseek-v4.1-flash"])
        with patch.object(llm, "test_server", return_value=probe), \
             patch.object(api, "getLLMModel",
                          return_value="deepseek/deepseek-v4.1-flash"), \
             patch.object(api.memory, "load_llm_server", return_value=""):
            client = api.application.test_client()
            response = client.get("/testConnection")
        body = response.get_json()
        self.assertTrue(body["model_found"])
        self.assertIsNone(body["batch_warning"])

    def test_unreachable_has_no_warning(self):
        probe = self._probe(reachable=False, error="connection refused")
        with patch.object(llm, "test_server", return_value=probe), \
             patch.object(api, "getLLMModel", return_value="x/y:batch"), \
             patch.object(api.memory, "load_llm_server", return_value=""):
            client = api.application.test_client()
            response = client.get("/testConnection")
        body = response.get_json()
        self.assertFalse(body["model_found"])
        self.assertIsNone(body["batch_warning"])


if __name__ == "__main__":
    unittest.main()
