"""Model sampling: persistence, validation, client wiring, the parameter-
rejection safeguard, and the API routes. No network calls (server URL faked)."""
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


class SamplingBase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        previous_directory = os.getcwd()
        os.chdir(self.directory.name)
        self.addCleanup(os.chdir, previous_directory)
        backend = str(Path(__file__).resolve().parents[1])
        sys.path.insert(0, backend)
        self.addCleanup(sys.path.remove, backend)
        for name in ("memory", "llm", "chat", "api"):
            sys.modules.pop(name, None)
        self.memory = importlib.import_module("memory")
        self.llm = importlib.import_module("llm")
        self.llm._REJECTED.clear()  # per-test isolation of the rejection memory

    @staticmethod
    def _with(settings, **enabled):
        out = dict(settings)
        for name, value in enabled.items():
            out[name] = {"enabled": True, "value": value}
        return out

    def _build(self, base_url, settings, enable_thinking=False):
        # Note: deliberately does NOT clear _REJECTED - a "retry" build must
        # see the rejection recorded by maybe_strip_rejected_params.
        self.llm.reset_llm()
        with patch.object(self.llm, "_server_url", return_value=base_url), \
             patch.object(self.memory, "load_sampling", return_value=settings):
            return self.llm.get_llm("sk-test", "test-model", enable_thinking=enable_thinking)


class SamplingPersistenceTests(SamplingBase):
    def test_default_is_all_disabled(self):
        settings = self.memory.load_sampling()
        self.assertEqual(sorted(settings), sorted(self.memory.SAMPLING_PARAMS))
        for entry in settings.values():
            self.assertFalse(entry["enabled"])
            self.assertIsNone(entry["value"])

    def test_save_load_roundtrip_with_clamping(self):
        saved = self.memory.save_sampling({
            "temperature": {"enabled": True, "value": 5.0},        # clamps to 2.0
            "top_k": {"enabled": True, "value": 9999},             # clamps to 200
            "presence_penalty": {"enabled": True, "value": -5},    # clamps to -2.0
            "max_tokens": {"enabled": True, "value": 600},
            "top_p": {"enabled": False, "value": 0.9},
        })
        self.assertEqual(saved["temperature"]["value"], 2.0)
        self.assertEqual(saved["top_k"]["value"], 200)
        self.assertEqual(saved["presence_penalty"]["value"], -2.0)
        self.assertEqual(saved["max_tokens"]["value"], 600)
        self.assertFalse(saved["top_p"]["enabled"])
        self.assertEqual(self.memory.load_sampling(), saved)

    def test_save_rejects_malformed_input(self):
        with self.assertRaises(ValueError):
            self.memory.save_sampling({"temperature": {"enabled": True, "value": "hot"}})
        with self.assertRaises(ValueError):
            self.memory.save_sampling({"temperature": "hot"})
        with self.assertRaises(ValueError):
            self.memory.save_sampling("not an object")
        with self.assertRaises(ValueError):
            self.memory.save_sampling(None)

    def test_corrupt_file_falls_back_to_default(self):
        self.memory.save_sampling({"temperature": {"enabled": True, "value": 1.0}})
        with open(self.memory.PATH_TO_SAMPLING, "w", encoding="utf-8") as f:
            f.write("this is not json{")
        self.assertEqual(self.memory.load_sampling(), self.memory.default_sampling())


class SamplingClientTests(SamplingBase):
    """The user's enabled settings reach the client, per host type."""

    def test_local_host_receives_all_enabled_params(self):
        settings = self.memory.default_sampling()
        settings = self._with(settings, temperature=0.7, top_k=40, min_p=0.05,
                              repetition_penalty=1.15, presence_penalty=0.3, max_tokens=777)
        client = self._build("http://localhost:8888/v1", settings)
        self.assertEqual(client.temperature, 0.7)
        self.assertEqual(client.presence_penalty, 0.3)
        self.assertEqual(client.max_tokens, 777)
        self.assertEqual(client.extra_body.get("top_k"), 40)
        self.assertEqual(client.extra_body.get("min_p"), 0.05)
        self.assertEqual(client.extra_body.get("repetition_penalty"), 1.15)

    def test_cloud_host_never_receives_local_only_params(self):
        settings = self.memory.default_sampling()
        settings = self._with(settings, temperature=0.9, top_k=40, min_p=0.05,
                              repetition_penalty=1.2)
        client = self._build("https://openrouter.ai/api/v1", settings)
        self.assertEqual(client.temperature, 0.9)
        extra = client.extra_body or {}
        for name in ("top_k", "min_p", "repetition_penalty"):
            self.assertNotIn(name, extra)

    def test_all_disabled_sends_nothing_new(self):
        client = self._build("http://localhost:8888/v1", self.memory.default_sampling())
        self.assertIsNone(client.temperature)
        self.assertEqual(client.max_tokens, 1024)  # built-in cap unchanged

    def test_thinking_client_keeps_4096_floor(self):
        small = self._with(self.memory.default_sampling(), max_tokens=256)
        client = self._build("http://localhost:8888/v1", small, enable_thinking=True)
        self.assertEqual(client.max_tokens, 4096)
        big = self._with(self.memory.default_sampling(), max_tokens=8192)
        client2 = self._build("http://localhost:8888/v1", big, enable_thinking=True)
        self.assertEqual(client2.max_tokens, 8192)

    def test_changed_settings_rebuild_the_client(self):
        first = self._build("http://localhost:8888/v1",
                            self._with(self.memory.default_sampling(), temperature=0.5))
        second = self._build("http://localhost:8888/v1",
                             self._with(self.memory.default_sampling(), temperature=1.1))
        self.assertEqual(first.temperature, 0.5)
        self.assertEqual(second.temperature, 1.1)


class ParamRejectionTests(SamplingBase):
    """The safeguard: a server that 400s on a sampling field is remembered,
    the client is rebuilt without it, and everything else is unaffected."""

    LOCAL = "http://localhost:8888/v1"

    @staticmethod
    def _error(status, text):
        exc = Exception(text)
        exc.status_code = status
        return exc

    def test_named_rejection_is_remembered_and_stripped(self):
        settings = self._with(self.memory.default_sampling(), top_p=0.9, temperature=0.7)
        self._build(self.LOCAL, settings)
        self.assertTrue(self.llm.maybe_strip_rejected_params(
            self._error(400, "Unrecognized field(s): top_p"), "test-model"))
        retry = self._build(self.LOCAL, settings)
        self.assertIsNone(retry.top_p)
        self.assertEqual(retry.temperature, 0.7)
        self.assertEqual(self.llm.rejected_sampling_params(self.LOCAL), ["top_p"])

    def test_unnamed_field_rejection_strips_all_sent_params(self):
        settings = self._with(self.memory.default_sampling(), top_p=0.9, temperature=0.7)
        self._build(self.LOCAL, settings)
        self.assertTrue(self.llm.maybe_strip_rejected_params(
            self._error(400, "Invalid request: unknown parameter provided"), "test-model"))
        retry = self._build(self.LOCAL, settings)
        self.assertIsNone(retry.top_p)
        self.assertIsNone(retry.temperature)

    def test_unrelated_errors_are_ignored(self):
        settings = self._with(self.memory.default_sampling(), top_p=0.9)
        self._build(self.LOCAL, settings)
        self.assertFalse(self.llm.maybe_strip_rejected_params(
            self._error(400, "Internal server error"), "test-model"))
        self.assertFalse(self.llm.maybe_strip_rejected_params(
            self._error(429, "Rate limit exceeded: invalid burst"), "test-model"))
        self.assertFalse(self.llm.maybe_strip_rejected_params(
            self._error(500, "unrecognized field: top_p"), "test-model"))
        self.assertFalse(self.llm.maybe_strip_rejected_params(
            self._error(400, "Invalid tool schema for AmadeusPack"), "test-model"))
        self.assertEqual(self.llm.rejected_sampling_params(self.LOCAL), [])

    def test_rejections_do_not_leak_across_hosts(self):
        settings = self._with(self.memory.default_sampling(), temperature=0.7)
        self._build(self.LOCAL, settings)
        self.assertTrue(self.llm.maybe_strip_rejected_params(
            self._error(400, "Unrecognized field(s): temperature"), "test-model"))
        cloud = self._build("https://openrouter.ai/api/v1", settings)
        self.assertEqual(cloud.temperature, 0.7)


class SamplingApiTests(SamplingBase):
    def setUp(self):
        super().setUp()
        self.api = importlib.import_module("api")
        self.client = self.api.application.test_client()

    def test_get_returns_settings_ranges_and_flags(self):
        response = self.client.get("/getSampling")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(len(data["params"]), len(self.memory.SAMPLING_PARAMS))
        self.assertEqual(data["local_only"], list(self.memory.SAMPLING_LOCAL_ONLY))
        self.assertIn("sampling", data)
        self.assertIn("rejected", data)

    def test_set_clamps_and_persists(self):
        response = self.client.post("/setSampling", json={
            "temperature": {"enabled": True, "value": 0.7},
            "top_k": {"enabled": True, "value": 9999},
        })
        self.assertEqual(response.status_code, 200)
        saved = response.get_json()["sampling"]
        self.assertEqual(saved["temperature"]["value"], 0.7)
        self.assertEqual(saved["top_k"]["value"], 200)
        self.assertEqual(self.memory.load_sampling(), saved)

    def test_set_rejects_malformed_input(self):
        response = self.client.post("/setSampling", json={
            "temperature": {"enabled": True, "value": "hot"},
        })
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/setSampling", json={"temperature": "hot"})
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/setSampling", data="not json",
                                    content_type="application/json")
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
