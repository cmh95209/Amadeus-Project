# -*- coding: utf-8 -*-
"""Offline checks for the LLM provider list (feature/provider-picker):
  1. the table itself (unique ids, cloud URLs https, no trailing slash);
  2. which provider a saved server address belongs to (blank, table URLs,
     trailing slash/case, the old Gemini endpoint, Vertex, custom);
  3. how existing installs map onto the table (address + api_key.txt, no
     provider file) and which key each provider gets;
  4. switching providers: the old key is stashed per provider, the new
     one goes into use ("" clears it), custom needs an address, Vertex
     needs a saved JSON key and signs in for its pass - the setup
     mistakes are refused before anything is written (a failed switch
     changes nothing);
  5. probes: Vertex via check_ready, everyone else via llm.test_server
     (stubbed - no network, no real keys);
  6. the background renewal: only while Vertex is active, only when due
     or forced, a failed sign-in never kills anything;
  7. the routes on the real Flask app (temp data folder): listing, the
     top "Save" (key + making the provider active at once, the saved
     model settled against the new server's list - kept / cleared /
     left as is), model probes, key reveal (this PC / own Tailscale
     tunnel / refused for phones and public funnels), saving the Vertex
     key (which makes Vertex active at once), and switching providers
     (a Vertex model is stored as google/<name>);
  8. vertex_auth itself: key validation, addresses, model names, the
     RS256 signature (verified with pow(sig, e, n) against a throwaway
     key built HERE - the repo holds no key file), and pass caching /
     renewal.
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND = Path(__file__).resolve().parents[1]
for _p in (BACKEND, Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import llm  # noqa: E402
import memory  # noqa: E402
import providers  # noqa: E402
import vertex_auth  # noqa: E402
from test_ceremony import _FreshAppTests  # noqa: E402


# ---------- throwaway RSA key (built here, never in the repo) --------------

def _is_probable_prime(n: int) -> bool:
    """A small Miller-Rabin (12 fixed bases) - plenty for a test key."""
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for a in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = (x * x) % n
            if x == n - 1:
                break
        else:
            return False
    return True


def _next_prime(seed: int) -> int:
    candidate = seed | 1
    while not _is_probable_prime(candidate):
        candidate += 2
    return candidate


_RSA_CACHE = None


def rsa_numbers():
    """(n, e, d) of a ~1200-bit key from two fixed 600-bit seeds: no key
    file ever lands in the repo or on the user's PC."""
    global _RSA_CACHE
    if _RSA_CACHE is None:
        p = _next_prime(2 ** 599 + 100003)
        q = _next_prime(2 ** 599 + 999983)
        n = p * q
        e = 65537
        _RSA_CACHE = (n, e, pow(e, -1, (p - 1) * (q - 1)))
    return _RSA_CACHE


def fake_vertex_key() -> str:
    """A JSON key that parses once vertex_auth._rsa_numbers is patched;
    the PEM inside is a placeholder the tests never feed to the real
    parser."""
    return json.dumps({
        "type": "service_account",
        "project_id": "amadeus-offline-test",
        "private_key_id": "offline-test",
        "private_key": "placeholder-not-a-real-key",
        "client_email": "amadeus-offline-test@amadeus-offline-test.iam.gserviceaccount.com",
    })


class _TempData(unittest.TestCase):
    """Sandbox for the personal files: providers.py and vertex_auth.py
    build every path from memory.DATA_DIR AT CALL TIME, and memory's
    PATH_TO_* point at temp files - no test write can land in the user's
    data folder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        self.data = d / "data"
        self.data.mkdir()
        for name, path in (("DATA_DIR", str(self.data)),
                           ("PATH_TO_API_KEY", str(self.data / "api_key.txt")),
                           ("PATH_TO_LLM_SERVER", str(d / "llm_server.txt")),
                           ("PATH_TO_LLM_MODEL", str(self.data / "llm_model.txt"))):
            p = patch.object(memory, name, path)
            p.start()
            self.addCleanup(p.stop)
        # The hooks api.py wires are process-wide: unit tests start clean
        # (a route test may have wired them to its own temp app).
        self._hooks = (providers.set_key_hook, providers.clear_key_hook)
        providers.set_key_hook = None
        providers.clear_key_hook = None
        self.addCleanup(setattr, providers, "set_key_hook", self._hooks[0])
        self.addCleanup(setattr, providers, "clear_key_hook", self._hooks[1])
        vertex_auth.forget_token()
        self.addCleanup(vertex_auth.forget_token)


class TableTests(_TempData):
    def test_ids_are_unique(self):
        ids = [row["id"] for row in providers.PROVIDERS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("auto", ids)
        self.assertIn("custom", ids)
        self.assertIn("vertex", ids)

    def test_every_row_has_label_kind_url(self):
        for row in providers.PROVIDERS:
            self.assertTrue(row["label"].strip(), row["id"])
            self.assertIn(row["kind"], ("local", "cloud", "vertex", "custom"), row["id"])
            self.assertIsInstance(row["url"], str, row["id"])

    def test_cloud_urls_are_https_without_a_trailing_slash(self):
        for row in providers.PROVIDERS:
            self.assertFalse(row["url"].endswith("/"), row["id"])
            if row["kind"] == "cloud":
                self.assertTrue(row["url"].startswith("https://"), row["id"])
        self.assertEqual(providers.get("auto")["url"], "")
        self.assertEqual(providers.get("vertex")["url"], "")
        self.assertEqual(providers.get("openrouter")["url"], "https://openrouter.ai/api/v1")
        self.assertIsNone(providers.get("nope"))


class AddressTests(_TempData):
    def test_blank_means_auto(self):
        self.assertEqual(providers.provider_for_address(""), "auto")
        self.assertEqual(providers.provider_for_address("   "), "auto")

    def test_every_table_url_maps_back_to_its_id(self):
        for row in providers.PROVIDERS:
            if row["url"]:
                self.assertEqual(providers.provider_for_address(row["url"]), row["id"])

    def test_trailing_slash_and_case_are_ignored(self):
        self.assertEqual(providers.provider_for_address("https://OpenRouter.AI/api/v1/"), "openrouter")
        self.assertEqual(providers.provider_for_address("HTTP://LOCALHOST:11434/V1/"), "ollama")

    def test_gemini_endpoints(self):
        # The old native endpoint maps to Gemini too...
        self.assertEqual(providers.provider_for_address(
            "https://generativelanguage.googleapis.com/v1beta"), "gemini")
        # ...and so does the new OpenAI-compat one (its table row).
        self.assertEqual(providers.provider_for_address(
            "https://generativelanguage.googleapis.com/v1beta/openai"), "gemini")

    def test_vertex_addresses(self):
        for url in ("https://aiplatform.googleapis.com/v1/projects/p/locations/global/endpoints/openapi",
                    "https://us-central1-aiplatform.googleapis.com/v1/projects/p/locations/us-central1/endpoints/openapi"):
            self.assertEqual(providers.provider_for_address(url), "vertex")

    def test_anything_else_is_custom(self):
        self.assertEqual(providers.provider_for_address("http://10.0.0.9:9999/v1"), "custom")
        self.assertEqual(providers.provider_for_address("https://example.com/v1"), "custom")


class ActiveIdTests(_TempData):
    def _set_file(self, value: str) -> None:
        (self.data / "llm_provider.txt").write_text(value, encoding="utf-8")

    def test_saved_file_wins_when_it_fits_the_address(self):
        memory.save_llm_server("https://api.deepseek.com")
        self._set_file("deepseek")
        self.assertEqual(providers.active_id(), "deepseek")

    def test_inconsistent_file_falls_back_to_the_address(self):
        memory.save_llm_server("https://openrouter.ai/api/v1")
        self._set_file("deepseek")  # wrong for this address
        self.assertEqual(providers.active_id(), "openrouter")

    def test_custom_fits_any_address_and_auto_needs_none(self):
        memory.save_llm_server("http://10.0.0.9:9999/v1")
        self._set_file("custom")
        self.assertEqual(providers.active_id(), "custom")
        memory.save_llm_server("")
        self._set_file("auto")
        self.assertEqual(providers.active_id(), "auto")

    def test_unknown_file_value_falls_back_to_the_address(self):
        memory.save_llm_server("https://openrouter.ai/api/v1")
        self._set_file("made-up")
        self.assertEqual(providers.active_id(), "openrouter")

    def test_vertex_file_needs_a_vertex_address(self):
        self._set_file("vertex")
        memory.save_llm_server("https://openrouter.ai/api/v1")
        self.assertEqual(providers.active_id(), "openrouter")
        memory.save_llm_server("https://aiplatform.googleapis.com/v1/projects/p/locations/global/endpoints/openapi")
        self.assertEqual(providers.active_id(), "vertex")


class ExistingInstallTests(_TempData):
    def test_address_plus_api_key_txt_becomes_the_matching_provider(self):
        # No llm_provider.txt: an install from before the picker existed.
        memory.save_llm_server("https://openrouter.ai/api/v1")
        memory.save_api_key("sk-or-test")
        self.assertEqual(providers.active_id(), "openrouter")
        self.assertFalse(providers.has_key("groq"))
        self.assertTrue(providers.has_key("openrouter"))
        self.assertEqual(providers.key_for("openrouter"), "sk-or-test")
        self.assertEqual(providers.key_for("groq"), "")
        self.assertEqual(providers.key_for("vertex"), "")

    def test_saved_key_outranks_api_key_txt(self):
        memory.save_llm_server("https://openrouter.ai/api/v1")
        memory.save_api_key("sk-or-old")
        providers.save_provider_key("openrouter", "sk-or-new")
        self.assertEqual(providers.key_for("openrouter"), "sk-or-new")
        # No hook is wired here: the file keeps the old key (the hooks
        # api.py wires would have applied the new one).
        self.assertEqual(memory.load_api_key(), "sk-or-old")


class ListingTests(_TempData):
    def test_shape_and_values(self):
        memory.save_llm_server("https://openrouter.ai/api/v1")
        memory.save_api_key("sk-or")
        data = providers.listing()
        self.assertEqual(data["active"], "openrouter")
        self.assertEqual(data["address"], "https://openrouter.ai/api/v1")
        self.assertFalse(data["vertex"]["configured"])
        self.assertEqual(data["vertex"]["region"], vertex_auth.DEFAULT_REGION)
        self.assertEqual(len(data["providers"]), len(providers.PROVIDERS))
        by_id = {row["id"]: row for row in data["providers"]}
        self.assertTrue(by_id["openrouter"]["has_key"])
        self.assertFalse(by_id["deepseek"]["has_key"])
        self.assertFalse(by_id["vertex"]["has_key"])


class SaveKeyTests(_TempData):
    def setUp(self):
        super().setUp()
        self.calls = []
        providers.set_key_hook = lambda k: self.calls.append(("set", k))
        providers.clear_key_hook = lambda: self.calls.append(("clear", ""))

    def test_key_for_the_active_provider_is_applied(self):
        memory.save_llm_server("https://api.groq.com/openai/v1")
        providers.save_provider_key("groq", "gsk-123")
        self.assertEqual(self.calls, [("set", "gsk-123")])
        self.assertEqual(providers.saved_keys(), {"groq": "gsk-123"})

    def test_key_for_another_provider_is_stored_only(self):
        memory.save_llm_server("https://api.groq.com/openai/v1")
        providers.save_provider_key("deepseek", "sk-ds")
        self.assertEqual(self.calls, [])
        self.assertEqual(providers.saved_keys(), {"deepseek": "sk-ds"})

    def test_rejections(self):
        with self.assertRaisesRegex(ValueError, "Unknown provider"):
            providers.save_provider_key("nope", "x")
        with self.assertRaisesRegex(ValueError, "Unknown provider"):
            providers.save_provider_key("", "x")
        with self.assertRaisesRegex(ValueError, "JSON key"):
            providers.save_provider_key("vertex", "x")
        with self.assertRaisesRegex(ValueError, "API key first"):
            providers.save_provider_key("groq", "   ")
        with self.assertRaisesRegex(ValueError, "API key first"):
            providers.save_provider_key("groq", "")


class ActivateTests(_TempData):
    def setUp(self):
        super().setUp()
        self.calls = []
        providers.set_key_hook = (lambda k: self.calls.append(("set", k))
                                  or memory.save_api_key(k))
        providers.clear_key_hook = (lambda: self.calls.append(("clear", ""))
                                    or memory.save_api_key(""))

    def test_stashes_the_old_key_and_swaps_in_the_new_one(self):
        # An existing install: OpenRouter, its key in api_key.txt. Groq has
        # a key of its own (saved from the new Settings).
        memory.save_llm_server("https://openrouter.ai/api/v1")
        memory.save_api_key("sk-or-test")
        providers.save_provider_key("groq", "gsk-test")
        result = providers.activate("groq")
        self.assertEqual(result, {"id": "groq", "address": "https://api.groq.com/openai/v1"})
        self.assertEqual(memory.load_llm_server(), "https://api.groq.com/openai/v1")
        self.assertEqual(memory.load_api_key(), "gsk-test")
        self.assertEqual(providers.active_id(), "groq")
        self.assertEqual((self.data / "llm_provider.txt").read_text(encoding="utf-8"), "groq")
        # The old key was stashed under OpenRouter, so switching back keeps it.
        self.assertEqual(providers.saved_keys()["openrouter"], "sk-or-test")
        self.assertEqual(providers.saved_keys()["groq"], "gsk-test")
        result = providers.activate("openrouter")
        self.assertEqual(result["address"], "https://openrouter.ai/api/v1")
        self.assertEqual(memory.load_api_key(), "sk-or-test")
        self.assertEqual(providers.active_id(), "openrouter")

    def test_provider_without_a_key_clears_the_live_key(self):
        memory.save_llm_server("https://openrouter.ai/api/v1")
        memory.save_api_key("sk-or-test")
        result = providers.activate("mistral")
        self.assertEqual(result["id"], "mistral")
        self.assertEqual(self.calls, [("clear", "")])
        self.assertEqual(memory.load_api_key(), "")

    def test_custom_uses_the_given_address_and_remembered_ones(self):
        result = providers.activate("custom", "http://10.0.0.9:9999/v1")
        self.assertEqual(result["address"], "http://10.0.0.9:9999/v1")
        result = providers.activate("custom")  # no new one: keep the saved
        self.assertEqual(result["address"], "http://10.0.0.9:9999/v1")

    def test_leaving_vertex_does_not_stash_its_pass(self):
        memory.save_llm_server("https://aiplatform.googleapis.com/v1/projects/p/locations/global/endpoints/openapi")
        (self.data / "llm_provider.txt").write_text("vertex", encoding="utf-8")
        memory.save_api_key("ya29-old-pass")
        providers.activate("groq")
        self.assertNotIn("vertex", providers.saved_keys())
        self.assertEqual(providers.active_id(), "groq")

    def test_activating_vertex_uses_the_signed_pass(self):
        n, e, d = rsa_numbers()
        with patch.object(vertex_auth, "_rsa_numbers", return_value=(n, e, d)), \
             patch.object(vertex_auth, "token", return_value="ya29-new-pass") as token, \
             patch.object(providers, "_apply_key") as apply:
            vertex_auth.save_key(fake_vertex_key(), "global")
            result = providers.activate("vertex")
        self.assertEqual(result["id"], "vertex")
        self.assertIn("aiplatform.googleapis.com", result["address"])
        self.assertIn("locations/global", result["address"])
        token.assert_called_once()
        apply.assert_called_once_with("ya29-new-pass")
        self.assertEqual((self.data / "llm_provider.txt").read_text(encoding="utf-8"), "vertex")
        self.assertIn("aiplatform.googleapis.com", memory.load_llm_server())


class ProbeTests(_TempData):
    OK = {"reachable": True, "models": ["m1"], "loading": False, "error": None}

    def test_vertex_maps_check_ready(self):
        with patch.object(vertex_auth, "check_ready", return_value={"ok": True, "error": None}):
            result = providers.probe("vertex")
        self.assertTrue(result["reachable"])
        self.assertEqual(result["models"], providers.VERTEX_MODELS)
        self.assertFalse(result["loading"])
        self.assertIsNone(result["error"])
        with patch.object(vertex_auth, "check_ready",
                          return_value={"ok": False, "error": "sign-in refused"}):
            result = providers.probe("vertex")
        self.assertFalse(result["reachable"])
        self.assertEqual(result["models"], [])
        self.assertEqual(result["error"], "sign-in refused")

    def test_cloud_probe_passes_the_table_address_and_key(self):
        memory.save_llm_server("https://api.deepseek.com")
        memory.save_api_key("sk-ds")
        refused = {"reachable": True, "models": [], "loading": False,
                   "error": "Server is running but rejected the API key (HTTP 401)"}
        with patch.object(llm, "test_server",
                          side_effect=[dict(self.OK), dict(refused)]) as probe:
            result = providers.probe("deepseek")
        self.assertEqual(result["models"], ["m1"])
        self.assertTrue(result["has_key"])
        self.assertTrue(result["key_checked"])
        self.assertEqual(probe.call_args_list[0][0], ("https://api.deepseek.com", "sk-ds"))
        self.assertEqual(probe.call_args_list[1][0],
                         ("https://api.deepseek.com", "invalid-key-check"))

    def test_auto_probe_uses_autodetect(self):
        with patch.object(llm, "autodetect_url", return_value="http://localhost:8080/v1"), \
             patch.object(llm, "test_server", return_value=dict(self.OK)) as probe:
            providers.probe("auto")
        probe.assert_called_once_with("http://localhost:8080/v1", "")

    def test_custom_needs_an_address(self):
        result = providers.probe("custom", "")
        self.assertFalse(result["reachable"])
        self.assertEqual(result["error"], "Enter the server address first.")
        with patch.object(llm, "test_server", return_value=dict(self.OK)) as probe:
            providers.probe("custom", "  http://10.0.0.9:9999/v1/ ")
        probe.assert_called_once_with("http://10.0.0.9:9999/v1/", "")

    def test_unknown_provider(self):
        with self.assertRaisesRegex(ValueError, "Unknown provider"):
            providers.probe("nope")


class RefreshTests(_TempData):
    def setUp(self):
        super().setUp()
        self.applied = []
        providers.set_key_hook = lambda k: self.applied.append(k)
        providers.clear_key_hook = lambda: None

    def _make_vertex_active(self):
        n, e, d = rsa_numbers()
        p = patch.object(vertex_auth, "_rsa_numbers", return_value=(n, e, d))
        p.start()
        self.addCleanup(p.stop)
        vertex_auth.save_key(fake_vertex_key(), "global")
        memory.save_llm_server("https://aiplatform.googleapis.com/v1/projects/amadeus-offline-test/"
                               "locations/global/endpoints/openapi")

    def test_noop_unless_vertex_is_active(self):
        memory.save_llm_server("https://openrouter.ai/api/v1")
        with patch.object(vertex_auth, "token") as token, \
             patch.object(vertex_auth, "token_age", return_value=99999.0):
            self.assertFalse(providers.refresh_vertex_if_due(force=True))
        token.assert_not_called()
        self.assertEqual(self.applied, [])

    def test_noop_while_the_pass_is_young(self):
        self._make_vertex_active()
        with patch.object(vertex_auth, "token") as token, \
             patch.object(vertex_auth, "token_age", return_value=10.0):
            self.assertFalse(providers.refresh_vertex_if_due())
        token.assert_not_called()

    def test_renews_when_the_pass_is_old(self):
        self._make_vertex_active()
        memory.save_api_key("ya29-old")
        with patch.object(vertex_auth, "token", return_value="ya29-new") as token, \
             patch.object(vertex_auth, "token_age", return_value=vertex_auth.REFRESH_AFTER + 1):
            self.assertTrue(providers.refresh_vertex_if_due())
        token.assert_called_once_with(force=False)
        self.assertEqual(self.applied, ["ya29-new"])

    def test_force_renews_even_a_young_pass(self):
        self._make_vertex_active()
        memory.save_api_key("ya29-old")
        with patch.object(vertex_auth, "token", return_value="ya29-new") as token, \
             patch.object(vertex_auth, "token_age", return_value=10.0):
            self.assertTrue(providers.refresh_vertex_if_due(force=True))
        token.assert_called_once_with(force=True)
        self.assertEqual(self.applied, ["ya29-new"])

    def test_same_pass_is_not_applied_twice(self):
        self._make_vertex_active()
        memory.save_api_key("ya29-old")
        with patch.object(vertex_auth, "token", return_value="ya29-old"), \
             patch.object(vertex_auth, "token_age", return_value=vertex_auth.REFRESH_AFTER + 1):
            self.assertFalse(providers.refresh_vertex_if_due())
        self.assertEqual(self.applied, [])

    def test_failed_sign_in_is_swallowed(self):
        self._make_vertex_active()
        with patch.object(vertex_auth, "token",
                          side_effect=vertex_auth.VertexSignInError("Google is down")), \
             patch.object(vertex_auth, "token_age", return_value=99999.0):
            self.assertFalse(providers.refresh_vertex_if_due(force=True))
        self.assertEqual(self.applied, [])

    def test_noop_while_the_builtin_model_is_active(self):
        # Her built-in brain ignores the custom pass: no renewal at all
        # while it is active (even forced) - the /setBrainMode route forces
        # a fresh pass on the switch back to custom mode.
        self._make_vertex_active()
        (self.data / "local_llm.json").write_text(
            json.dumps({"mode": "builtin"}), encoding="utf-8")
        with patch.object(vertex_auth, "token") as token, \
              patch.object(vertex_auth, "token_age", return_value=99999.0):
            self.assertFalse(providers.refresh_vertex_if_due(force=True))
        token.assert_not_called()
        self.assertEqual(self.applied, [])


class ParseKeyTests(_TempData):
    def test_bad_json(self):
        with self.assertRaisesRegex(ValueError, "not a JSON key"):
            vertex_auth.parse_key("definitely not json")
        with self.assertRaisesRegex(ValueError, "not a JSON key"):
            vertex_auth.parse_key("")

    def test_wrong_type(self):
        data = {"type": "other", "project_id": "p", "private_key": "x", "client_email": "e"}
        with self.assertRaisesRegex(ValueError, "service account"):
            vertex_auth.parse_key(json.dumps(data))

    def test_missing_fields(self):
        for field in ("project_id", "private_key", "client_email"):
            data = {"type": "service_account", "project_id": "p",
                    "private_key": "x", "client_email": "e"}
            del data[field]
            with patch.object(vertex_auth, "_rsa_numbers", return_value=(2 ** 1025, 65537, 1)):
                with self.assertRaisesRegex(ValueError, field):
                    vertex_auth.parse_key(json.dumps(data))

    def test_unreadable_private_key(self):
        data = {"type": "service_account", "project_id": "p",
                "private_key": "garbage", "client_email": "e"}
        with self.assertRaisesRegex(ValueError, "could not be read"):
            vertex_auth.parse_key(json.dumps(data))

    def test_base_url_global_and_regional(self):
        self.assertEqual(
            vertex_auth.base_url("my-project", "global"),
            "https://aiplatform.googleapis.com/v1/projects/my-project/locations/global/endpoints/openapi")
        self.assertEqual(
            vertex_auth.base_url("my-project", "us-central1"),
            "https://us-central1-aiplatform.googleapis.com/v1/projects/my-project/locations/us-central1/endpoints/openapi")

    def test_model_name_gets_the_google_prefix(self):
        self.assertEqual(vertex_auth.model_name("gemini-3.8-flash"), "google/gemini-3.8-flash")
        self.assertEqual(vertex_auth.model_name("google/gemini-3.8-flash"), "google/gemini-3.8-flash")
        self.assertEqual(vertex_auth.model_name("  "), "")

    def test_clean_region(self):
        self.assertEqual(vertex_auth.clean_region(""), "global")
        self.assertEqual(vertex_auth.clean_region(None), "global")
        self.assertEqual(vertex_auth.clean_region(" US-Central1 "), "us-central1")
        for bad in ("us_central1", "us-central1;rm", "eu west1"):
            with self.assertRaises(ValueError):
                vertex_auth.clean_region(bad)


class SignTests(_TempData):
    def test_rs256_signature_verifies_with_the_public_numbers(self):
        n, e, d = rsa_numbers()
        with patch.object(vertex_auth, "_rsa_numbers", return_value=(n, e, d)):
            signature = vertex_auth._sign_rs256(b"header.claims", "irrelevant-pem")
        size = (n.bit_length() + 7) // 8
        self.assertEqual(len(signature), size)
        # Recompute the PKCS#1 v1.5 padding and check pow(sig, e, n) against
        # it: a correct signature round-trips through the public numbers.
        digest = vertex_auth._SHA256_PREFIX + hashlib.sha256(b"header.claims").digest()
        padded = b"\x00\x01" + b"\xff" * (size - len(digest) - 3) + b"\x00" + digest
        self.assertEqual(pow(int.from_bytes(signature, "big"), e, n),
                         int.from_bytes(padded, "big"))


class TokenTests(_TempData):
    def setUp(self):
        super().setUp()
        n, e, d = rsa_numbers()
        p = patch.object(vertex_auth, "_rsa_numbers", return_value=(n, e, d))
        p.start()
        self.addCleanup(p.stop)
        vertex_auth.save_key(fake_vertex_key(), "global")

    def test_token_caches_until_it_is_due(self):
        now = [1000.0]
        with patch.object(vertex_auth, "time") as fake_time, \
             patch.object(vertex_auth, "_fetch_token",
                          side_effect=["ya29-old", "ya29-new"]) as fetch:
            fake_time.time = lambda: now[0]
            self.assertEqual(vertex_auth.token(), "ya29-old")
            now[0] += vertex_auth.REFRESH_AFTER - 1
            self.assertEqual(vertex_auth.token(), "ya29-old")   # inside the window
            now[0] += 2
            self.assertEqual(vertex_auth.token(), "ya29-new")   # due: renewed
        self.assertEqual(fetch.call_count, 2)

    def test_failed_renewal_keeps_the_old_pass_inside_its_hour(self):
        now = [1000.0]
        with patch.object(vertex_auth, "time") as fake_time, \
             patch.object(vertex_auth, "_fetch_token", side_effect=[
                 "ya29-old",
                 vertex_auth.VertexSignInError("Google is down"),
                 vertex_auth.VertexSignInError("still down")]):
            fake_time.time = lambda: now[0]
            self.assertEqual(vertex_auth.token(), "ya29-old")
            now[0] += vertex_auth.REFRESH_AFTER + 60  # due, the renewal fails...
            self.assertEqual(vertex_auth.token(), "ya29-old")  # ...old pass still inside its hour
            now[0] += 721  # now outside the hour (3600 - 120)
            with self.assertRaises(vertex_auth.VertexSignInError):
                vertex_auth.token()

    def test_no_saved_key_raises(self):
        (self.data / "vertex_key.json").unlink()
        with self.assertRaisesRegex(vertex_auth.VertexSignInError, "No Google Vertex JSON key"):
            vertex_auth.token()


class RouteTests(_FreshAppTests):
    """The routes on the real Flask app against a temp data folder (llm
    stubbed - no network). The shared harness reimports the whole stack per
    test, so the routes run on the FRESH modules - and every patch below
    must target those (self.providers / self.vertex_auth), not the
    collection-time copies imported at the top of this file."""

    def setUp(self):
        super().setUp()
        self.providers = self.api.providers
        self.vertex_auth = self.api.vertex_auth
        # Belt and braces: the fresh providers already runs against this
        # test's memory, but pin it (and the one absolute path,
        # llm_server.txt) explicitly.
        p = patch.object(self.providers, "memory", self.memory)
        p.start()
        self.addCleanup(p.stop)
        s = patch.object(self.memory, "PATH_TO_LLM_SERVER",
                         str(Path(self.directory.name) / "llm_server.txt"))
        s.start()
        self.addCleanup(s.stop)
        self.vertex_auth.forget_token()
        self.addCleanup(self.vertex_auth.forget_token)

    def test_listing_shape(self):
        res = self.client.get("/llm/providers")
        self.assertEqual(res.status_code, 200)
        data = res.json
        self.assertEqual([row["id"] for row in data["providers"]],
                         [row["id"] for row in providers.PROVIDERS])
        for row in data["providers"]:
            for field in ("id", "label", "kind", "url", "has_key"):
                self.assertIn(field, row)
        self.assertEqual(data["active"], "auto")
        self.assertEqual(data["address"], "")
        self.assertFalse(data["vertex"]["configured"])
        self.assertEqual(data["vertex"]["region"], self.vertex_auth.DEFAULT_REGION)
        self.assertTrue(data["can_reveal"])  # the test client sits "on the PC"

    def test_save_key_stores_and_probes(self):
        res = self.client.post("/llm/key", json={"provider": "groq", "key": "  gsk-test  "})
        self.assertEqual(res.status_code, 200)
        body = res.json
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["has_key"])
        self.assertIn("reachable", body)  # the stubbed probe ran, no network
        keys = json.loads(Path("data/api_keys.json").read_text(encoding="utf-8"))
        self.assertEqual(keys, {"groq": "gsk-test"})
        # The top Save made Groq the active connection at once: its address
        # + key in use, the reply's listing fields fresh.
        self.assertEqual(body["active"], "groq")
        self.assertEqual(body["address"], "https://api.groq.com/openai/v1")
        self.assertEqual(body["model"], "")  # nothing was saved before
        self.assertEqual(self.providers.active_id(), "groq")
        self.assertEqual(self.memory.load_llm_server(), "https://api.groq.com/openai/v1")
        self.assertEqual(self.memory.load_api_key(), "gsk-test")

    def test_top_save_switches_the_active_provider_with_an_empty_key(self):
        # OpenRouter active with its key in use: the top Save on Groq (box
        # empty) switches the connection at once - Groq's address, and no
        # key in use (it has none saved); OpenRouter's key is stashed under
        # its own name.
        self.memory.save_llm_server("https://openrouter.ai/api/v1")
        self.memory.save_api_key("sk-or")
        res = self.client.post("/llm/key", json={"provider": "groq", "key": ""})
        self.assertEqual(res.status_code, 200)
        body = res.json
        self.assertEqual(body["active"], "groq")
        self.assertFalse(body["has_key"])
        self.assertEqual(self.providers.active_id(), "groq")
        self.assertEqual(self.memory.load_llm_server(), "https://api.groq.com/openai/v1")
        self.assertEqual(self.memory.load_api_key(), "")
        self.assertEqual(self.providers.saved_keys()["openrouter"], "sk-or")

    def test_top_save_custom_needs_an_address_and_changes_nothing(self):
        # "Custom address" with an empty address: 400, plain message, and
        # NOTHING is changed (no provider file, no stash, no key swap).
        self.memory.save_llm_server("https://openrouter.ai/api/v1")
        self.memory.save_api_key("sk-or")
        self.memory.save_llm_model("llama-x")
        res = self.client.post("/llm/key",
                               json={"provider": "custom", "key": "", "address": ""})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json["message"], "Enter the server address first.")
        self.assertEqual(self.providers.active_id(), "openrouter")
        self.assertEqual(self.memory.load_llm_server(), "https://openrouter.ai/api/v1")
        self.assertEqual(self.memory.load_api_key(), "sk-or")
        self.assertEqual(self.memory.load_llm_model(), "llama-x")
        self.assertFalse(Path("data/llm_provider.txt").exists())
        self.assertFalse(Path("data/api_keys.json").exists())

    def test_save_key_rejections(self):
        self.assertEqual(self.client.post("/llm/key", json={"provider": "nope", "key": "x"}).status_code, 400)
        self.assertEqual(self.client.post("/llm/key", json={"provider": "vertex", "key": "x"}).status_code, 400)
        self.assertEqual(self.client.post("/llm/key", json={"provider": "groq"}).status_code, 400)

    def test_save_key_an_empty_key_checks_without_saving(self):
        # The button may be pressed with an empty box: nothing is stored
        # (a saved key is left untouched) and the provider is made active
        # at once (its saved key goes into use); the server is checked
        # either way.
        res = self.client.post("/llm/key", json={"provider": "groq", "key": "sk-first"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json["active"], "groq")
        res = self.client.post("/llm/key", json={"provider": "groq", "key": "   "})
        self.assertEqual(res.status_code, 200)
        body = res.json
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["has_key"])        # the saved key stays
        self.assertIn("reachable", body)        # the (stubbed) probe ran
        self.assertEqual(body["active"], "groq")  # the second press is a no-op switch
        keys = json.loads(Path("data/api_keys.json").read_text(encoding="utf-8"))
        self.assertEqual(keys, {"groq": "sk-first"})
        self.assertEqual(self.memory.load_api_key(), "sk-first")

    def test_save_key_an_empty_key_with_none_saved_stays_none(self):
        res = self.client.post("/llm/key", json={"provider": "openrouter", "key": ""})
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json["has_key"])
        self.assertFalse(self.providers.has_key("openrouter"))
        self.assertEqual(res.json["active"], "openrouter")  # made active anyway
        self.assertEqual(self.providers.active_id(), "openrouter")

    def test_save_key_for_the_active_provider_applies_it(self):
        res = self.client.post("/llm/provider", json={"id": "openrouter"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json["active"], "openrouter")
        res = self.client.post("/llm/key", json={"provider": "openrouter", "key": "sk-new"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.memory.load_api_key(), "sk-new")
        self.assertEqual(self.chat.API_KEY, "sk-new")

    def _switch_to_groq_with_list(self, models, reachable=True):
        """OpenRouter active (key in use, model "llama-x" saved - set the
        way the app saves it: file AND the in-use state); the top Save on
        Groq, whose server lists `models` (the patched probe)."""
        self.memory.save_llm_server("https://openrouter.ai/api/v1")
        self.memory.save_api_key("sk-or")
        self.chat.setLLMModel("llama-x")

        def fake_test_server(base_url, api_key="", timeout=5.0):
            if not reachable:
                return {"reachable": False, "models": [], "loading": False,
                        "error": "ConnectionRefusedError: stub"}
            if api_key == "invalid-key-check":
                return {"reachable": True, "models": [], "loading": False,
                        "error": "Server is running but rejected the API key (HTTP 401)"}
            return {"reachable": True, "models": list(models),
                    "loading": False, "error": None}
        with patch.object(self.providers.llm, "test_server",
                          side_effect=fake_test_server):
            res = self.client.post("/llm/key",
                                   json={"provider": "groq", "key": "gsk-new"})
        return res

    def test_top_save_keeps_a_model_the_new_server_offers(self):
        res = self._switch_to_groq_with_list(["llama-x", "other"])
        self.assertEqual(res.status_code, 200)
        body = res.json
        self.assertEqual(body["active"], "groq")
        self.assertEqual(body["model"], "llama-x")
        self.assertEqual(self.providers.active_id(), "groq")
        # Kept: the new server's list offers it, so she keeps talking to it.
        self.assertEqual(self.memory.load_llm_model(), "llama-x")
        self.assertEqual(self.chat.LLM_Model, "llama-x")

    def test_top_save_clears_a_model_the_new_server_does_not_offer(self):
        res = self._switch_to_groq_with_list(["only-this-model"])
        self.assertEqual(res.status_code, 200)
        body = res.json
        self.assertEqual(body["active"], "groq")
        self.assertEqual(body["model"], "")
        self.assertEqual(self.providers.active_id(), "groq")
        # Cleared (the "No Model Selected." state): a model name from the
        # previous provider must not go to the new server.
        self.assertEqual(self.memory.load_llm_model(), "")
        self.assertEqual(self.chat.LLM_Model, "")
        self.assertEqual(self.chat.getLLMModel(), "No Model Selected.")

    def test_top_save_keeps_the_model_when_the_list_cannot_load(self):
        # The server never answered: the saved model is left as is (the
        # footer dot will report the unreachable server).
        res = self._switch_to_groq_with_list([], reachable=False)
        self.assertEqual(res.status_code, 200)
        body = res.json
        self.assertEqual(body["active"], "groq")
        self.assertEqual(body["model"], "llama-x")
        self.assertEqual(self.memory.load_llm_model(), "llama-x")
        self.assertEqual(self.chat.LLM_Model, "llama-x")

    def test_models_route(self):
        res = self.client.post("/llm/models", json={"provider": "deepseek"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("reachable", res.json)
        self.assertEqual(self.client.post("/llm/models", json={"provider": "nope"}).status_code, 400)
        self.assertEqual(self.client.post("/llm/models", json={}).status_code, 400)

    def test_reveal_from_this_pc(self):
        self.client.post("/llm/key", json={"provider": "groq", "key": "gsk-test"})
        res = self.client.get("/llm/key", query_string={"provider": "groq"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json["key"], "gsk-test")
        self.assertEqual(res.headers["Cache-Control"], "no-store")

    def test_reveal_allowed_through_the_users_own_tunnel(self):
        self.client.post("/llm/key", json={"provider": "groq", "key": "gsk-test"})
        res = self.client.get("/llm/key", query_string={"provider": "groq"},
                              headers={"Tailscale-User-Login": "mh"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json["key"], "gsk-test")

    def test_reveal_refused_for_a_phone(self):
        self.client.post("/llm/key", json={"provider": "groq", "key": "gsk-test"})
        res = self.client.get("/llm/key", query_string={"provider": "groq"},
                              headers={"X-Forwarded-For": "100.64.0.7"})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json["message"],
                         "Open Settings on the PC or one of your own devices to view the key.")
        # Vertex and unknown providers are 400 even from this PC.
        self.assertEqual(self.client.get("/llm/key", query_string={"provider": "vertex"}).status_code, 400)
        self.assertEqual(self.client.get("/llm/key", query_string={"provider": "nope"}).status_code, 400)

    def test_vertex_route_saves_key_region_and_probe(self):
        # "Save JSON key": the key is stored AND Vertex becomes the active
        # provider at once (pass + per-project address into use).
        n, e, d = rsa_numbers()
        with patch.object(self.vertex_auth, "_rsa_numbers", return_value=(n, e, d)), \
             patch.object(self.vertex_auth, "token", return_value="ya29-offline-pass"), \
             patch.object(self.vertex_auth, "check_ready", return_value={"ok": True, "error": None}):
            res = self.client.post("/llm/vertex",
                                   json={"json": fake_vertex_key(), "region": "us-central1"})
        self.assertEqual(res.status_code, 200)
        body = res.json
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["vertex"]["configured"])
        self.assertEqual(body["vertex"]["region"], "us-central1")
        self.assertEqual(body["vertex"]["project"], "amadeus-offline-test")
        self.assertIn("us-central1-aiplatform", body["vertex"]["address"])
        self.assertTrue(body["reachable"])
        self.assertEqual(body["models"], self.providers.VERTEX_MODELS)
        self.assertEqual(body["active"], "vertex")
        self.assertEqual(self.providers.active_id(), "vertex")
        self.assertIn("aiplatform.googleapis.com", self.memory.load_llm_server())
        self.assertEqual(self.memory.load_api_key(), "ya29-offline-pass")

    def test_vertex_route_sign_in_failure_keeps_the_previous_provider(self):
        # The key is stored, but Google refuses the sign-in: the switch
        # does not happen, the saved key stands, and the failure is
        # reported (not raised).
        self.memory.save_llm_server("https://openrouter.ai/api/v1")
        self.memory.save_api_key("sk-or")
        n, e, d = rsa_numbers()
        with patch.object(self.vertex_auth, "_rsa_numbers", return_value=(n, e, d)), \
             patch.object(self.vertex_auth, "token",
                          side_effect=self.vertex_auth.VertexSignInError(
                              "Google Vertex refused the sign-in.")), \
             patch.object(self.vertex_auth, "check_ready",
                          return_value={"ok": False,
                                        "error": "Google Vertex refused the sign-in."}):
            res = self.client.post("/llm/vertex", json={"json": fake_vertex_key()})
        self.assertEqual(res.status_code, 200)
        body = res.json
        self.assertIn("message", body)
        self.assertIn("refused the sign-in", body["message"])
        self.assertEqual(body["active"], "openrouter")
        self.assertEqual(self.providers.active_id(), "openrouter")
        self.assertEqual(self.memory.load_llm_server(), "https://openrouter.ai/api/v1")
        self.assertTrue(body["vertex"]["configured"])  # the saved key stands

    def test_vertex_route_rejects_bad_input(self):
        res = self.client.post("/llm/vertex", json={"json": "not a JSON key"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("JSON", res.json["message"])
        self.assertEqual(self.client.post("/llm/vertex", json={}).status_code, 400)

    def test_vertex_route_region_only(self):
        res = self.client.post("/llm/vertex", json={"region": "europe-west1"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json["vertex"]["region"], "europe-west1")
        self.assertFalse(res.json["vertex"]["configured"])
        self.assertFalse(res.json["reachable"])  # no key saved yet (offline)

    def test_provider_route_switches_to_vertex_and_prefixes_the_model(self):
        n, e, d = rsa_numbers()
        with patch.object(self.vertex_auth, "_rsa_numbers", return_value=(n, e, d)), \
             patch.object(self.vertex_auth, "token", return_value="ya29-offline-pass") as token:
            self.vertex_auth.save_key(fake_vertex_key(), "global")
            res = self.client.post("/llm/provider",
                                   json={"id": "vertex", "model": "gemini-3.8-flash"})
        self.assertEqual(res.status_code, 200)
        body = res.json
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["active"], "vertex")
        self.assertEqual(body["model"], "google/gemini-3.8-flash")
        self.assertIn("aiplatform.googleapis.com", body["address"])
        self.assertEqual(self.memory.load_llm_model(), "google/gemini-3.8-flash")
        self.assertEqual(self.memory.load_api_key(), "ya29-offline-pass")
        token.assert_called_once()

    def test_provider_route_vertex_without_a_key_is_400(self):
        res = self.client.post("/llm/provider", json={"id": "vertex"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json["message"], "Save your Google Vertex JSON key first.")

    def test_provider_route_custom_needs_an_address(self):
        res = self.client.post("/llm/provider", json={"id": "custom"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json["message"], "Enter the server address first.")
        res = self.client.post("/llm/provider", json={"id": "custom",
                                                      "address": "http://10.0.0.9:9999/v1"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json["address"], "http://10.0.0.9:9999/v1")

    def test_provider_route_unknown_is_400(self):
        self.assertEqual(self.client.post("/llm/provider", json={"id": "nope"}).status_code, 400)

    def test_brain_mode_back_to_custom_renews_the_vertex_pass(self):
        # Path A: her built-in model is active while Vertex is the saved
        # custom provider - the keeper pauses, so switching back to custom
        # mode renews the pass at once (the first chat must not race the
        # keeper's next round).
        from test_builtin_llm import _config, _write
        n, e, d = rsa_numbers()
        _write(Path("data") / "local_llm.json", _config(Path(self.directory.name)))
        with patch.object(self.vertex_auth, "_rsa_numbers", return_value=(n, e, d)):
            self.vertex_auth.save_key(fake_vertex_key(), "global")
        self.memory.save_llm_server("https://aiplatform.googleapis.com/v1/projects/"
                                    "amadeus-offline-test/locations/global/endpoints/openapi")
        # While her built-in brain is active: even a forced renewal is off.
        with patch.object(self.vertex_auth, "token") as token:
            self.assertFalse(self.providers.refresh_vertex_if_due(force=True))
        token.assert_not_called()
        # The switch back: one forced renewal, the new pass in use at once.
        with patch.object(self.vertex_auth, "token", return_value="ya29-fresh") as token:
            res = self.client.post("/setBrainMode", json={"mode": "custom"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json["mode"], "custom")
        token.assert_called_once_with(force=True)
        self.assertEqual(self.memory.load_api_key(), "ya29-fresh")
        self.assertEqual(self.chat.API_KEY, "ya29-fresh")

    def test_test_route_ok(self):
        # One message through the model in her own format: 200 + the words.
        with patch.object(self.providers.llm, "test_server",
                          return_value={"reachable": True, "models": ["m1"],
                                        "loading": False, "error": None}):
            self.client.post("/llm/key", json={"provider": "groq", "key": "gsk-test"})
            fake = _FakeClient(structured=_FakePack("やあ。", "Hello."))
            with patch.object(self.providers.llm, "client_for", return_value=fake):
                res = self.client.post("/llm/test",
                                       json={"provider": "groq", "model": "llama-x"})
        self.assertEqual(res.status_code, 200)
        body = res.json
        for field in ("ok", "outcome", "message", "model", "seconds"):
            self.assertIn(field, body)
        self.assertTrue(body["ok"])
        self.assertEqual(body["outcome"], "ok")
        self.assertEqual(body["model"], "llama-x")
        self.assertIn("Works with Amadeus", body["message"])

    def test_test_route_no_model_and_unknown_provider(self):
        res = self.client.post("/llm/test", json={"provider": "groq", "model": ""})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json["outcome"], "no_model")
        self.assertEqual(res.json["message"], "Pick a model first.")
        self.assertEqual(self.client.post("/llm/test",
                                          json={"provider": "nope"}).status_code, 400)
        self.assertEqual(self.client.post("/llm/test", json={}).status_code, 400)

    def test_test_route_every_outcome_is_200(self):
        with patch.object(self.providers.llm, "test_server",
                          return_value={"reachable": True, "models": ["m1"],
                                        "loading": False, "error": None}):
            self.client.post("/llm/key", json={"provider": "groq", "key": "gsk-test"})
        scenarios = [
            (_FakeClient(structured_exc=_StatusError(401, "invalid key")), "bad_key"),
            (_FakeClient(structured_exc=_StatusError(404, "model not found")), "model_missing"),
            (_FakeClient(structured_exc=_StatusError(402, "no credit")), "no_credit"),
            (_FakeClient(structured_exc=_StatusError(429, "Too many requests")), "rate_limited"),
            (_FakeClient(structured=None, plain=_FakePlain("hi")), "wrong_format"),
            (_FakeClient(structured_exc=APIConnectionError("refused")), "unreachable"),
        ]
        for fake, outcome in scenarios:
            with patch.object(self.providers.llm, "client_for", return_value=fake):
                res = self.client.post("/llm/test",
                                       json={"provider": "groq", "model": "llama-x"})
            self.assertEqual(res.status_code, 200, outcome)
            self.assertEqual(res.json["outcome"], outcome, outcome)

    def test_test_route_changes_nothing_saved(self):
        with patch.object(self.providers.llm, "test_server",
                          return_value={"reachable": True, "models": ["m1"],
                                        "loading": False, "error": None}):
            self.client.post("/llm/key", json={"provider": "groq", "key": "gsk-test"})
            res = self.client.post("/llm/provider",
                                   json={"id": "groq", "model": "llama-x"})
        self.assertEqual(res.status_code, 200)
        api_key_before = self.memory.load_api_key()
        model_before = self.memory.load_llm_model()
        address_before = self.memory.load_llm_server()
        keys_before = json.loads(Path("data/api_keys.json").read_text(encoding="utf-8"))
        fake = _FakeClient(structured=_FakePack("やあ。", "Hello."))
        with patch.object(self.providers.llm, "client_for", return_value=fake):
            res = self.client.post("/llm/test",
                                   json={"provider": "groq", "model": "llama-x"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json["ok"])
        self.assertEqual(self.memory.load_api_key(), api_key_before)
        self.assertEqual(self.memory.load_llm_model(), model_before)
        self.assertEqual(self.memory.load_llm_server(), address_before)
        self.assertEqual(json.loads(Path("data/api_keys.json").read_text(encoding="utf-8")),
                         keys_before)
        self.assertEqual(self.chat.API_KEY, api_key_before)

    def test_models_route_reports_the_key_facts(self):
        # The reply carries has_key + key_checked: a refused bogus key proves
        # the saved one; a provider that lists models with any key proves
        # nothing (one extra /models call with a bogus key).
        OK = {"reachable": True, "models": ["m1"], "loading": False, "error": None}
        REFUSED = {"reachable": True, "models": [], "loading": False,
                   "error": "Server is running but rejected the API key (HTTP 401)"}
        with patch.object(self.providers.llm, "test_server",
                          side_effect=[dict(OK), dict(OK), dict(OK), dict(REFUSED)]):
            self.client.post("/llm/key", json={"provider": "deepseek", "key": "sk-test"})
            res = self.client.post("/llm/models", json={"provider": "deepseek"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json["has_key"])
        self.assertTrue(res.json["key_checked"])
        with patch.object(self.providers.llm, "test_server",
                          side_effect=[dict(OK), dict(OK), dict(OK), dict(OK)]):
            self.client.post("/llm/key", json={"provider": "openrouter", "key": "sk-or"})
            res = self.client.post("/llm/models", json={"provider": "openrouter"})
        self.assertFalse(res.json["key_checked"])

    def test_vertex_route_keeps_the_saved_region_when_none_is_sent(self):
        n, e, d = rsa_numbers()
        with patch.object(self.vertex_auth, "_rsa_numbers", return_value=(n, e, d)), \
             patch.object(self.vertex_auth, "token", return_value="ya29-offline-pass"), \
             patch.object(self.vertex_auth, "check_ready", return_value={"ok": True, "error": None}):
            res = self.client.post("/llm/vertex",
                                   json={"json": fake_vertex_key(), "region": "us-central1"})
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json["active"], "vertex")  # active at once
            # The page now saves the JSON key WITHOUT a region: the saved
            # region must survive.
            res = self.client.post("/llm/vertex", json={"json": fake_vertex_key()})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json["vertex"]["region"], "us-central1")
        self.assertIn("us-central1-aiplatform", res.json["vertex"]["address"])
        self.assertTrue(res.json["vertex"]["configured"])


# ---------- llm.py: the pieces this feature added ----------

class _Refused(Exception):
    status_code = 400

    def __str__(self):
        return "Bad Request"


class SafetyNetTests(unittest.TestCase):
    """A cloud provider that answers 400 to the plain reasoning_effort field
    without saying why gets one try without it (llm._retry_without_effort)."""

    def _run(self, fail_times, extra_body):
        import io
        from contextlib import redirect_stdout
        client = llm._ThoughtFreeChatOpenAI(model="m", api_key="k", base_url="https://api.groq.com/openai/v1",
                                            extra_body=extra_body)
        calls = []

        def fake(self_, messages, stop=None, run_manager=None, **kw):
            calls.append(dict(self_.extra_body or {}))
            if len(calls) <= fail_times:
                raise _Refused()
            return "result"

        with patch.object(llm.ChatOpenAI, "_generate", fake), redirect_stdout(io.StringIO()):
            try:
                return client._generate([]), calls, client
            except _Refused as exc:
                return exc, calls, client

    def test_works_without_the_field_and_keeps_it_off(self):
        result, calls, client = self._run(1, {"reasoning_effort": "low", "top_k": 5})
        self.assertEqual(result, "result")
        self.assertEqual(calls, [{"reasoning_effort": "low", "top_k": 5}, {"top_k": 5}])
        self.assertEqual(client.extra_body, {"top_k": 5})

    def test_still_refused_puts_the_setting_back_and_raises_the_first_error(self):
        result, calls, client = self._run(2, {"reasoning_effort": "low"})
        self.assertIsInstance(result, _Refused)
        self.assertEqual(len(calls), 2)
        self.assertEqual(client.extra_body, {"reasoning_effort": "low"})

    def test_no_retry_when_no_effort_field_was_sent(self):
        result, calls, _client = self._run(1, {"top_k": 5})
        self.assertIsInstance(result, _Refused)
        self.assertEqual(len(calls), 1)


class VertexReasoningTests(unittest.TestCase):
    def test_vertex_counts_as_a_gemini_host(self):
        self.assertTrue(llm._is_gemini_host(vertex_auth.base_url("p", "global")))
        self.assertTrue(llm._is_gemini_host("https://generativelanguage.googleapis.com/v1beta/openai"))
        self.assertFalse(llm._is_gemini_host("https://openrouter.ai/api/v1"))

    def _extra(self, url, level):
        llm.reset_llm()
        with patch.object(llm, "_server_url", return_value=url),                 patch.object(llm.builtin_llm, "active", return_value=False),                 patch.object(memory, "load_sampling", return_value=memory.default_sampling()):
            client = llm.get_llm("k", "m", reasoning=level)
        llm.reset_llm()
        return client.extra_body

    def test_off_sends_low_on_vertex_and_minimal_on_the_gemini_api(self):
        vertex = vertex_auth.base_url("p", "global")
        gemini = "https://generativelanguage.googleapis.com/v1beta/openai"
        self.assertEqual(self._extra(vertex, "off"), {"reasoning_effort": "low"})
        self.assertEqual(self._extra(vertex, "high"), {"reasoning_effort": "high"})
        self.assertEqual(self._extra(gemini, "off"), {"reasoning_effort": "minimal"})


class WrongKeyProbeTests(unittest.TestCase):
    def test_http_400_about_the_api_key_reads_as_a_rejected_key(self):
        class _Reply:
            status_code = 400
            text = '{"error": {"message": "Please pass a valid API key"}}'

        with patch.object(llm.requests, "get", return_value=_Reply()):
            result = llm.test_server("https://generativelanguage.googleapis.com/v1beta/openai", "bad")
        self.assertTrue(result["reachable"])
        self.assertIn("rejected the API key", result["error"])

    def test_other_http_400_is_unchanged(self):
        class _Reply:
            status_code = 400
            text = "something else"

        with patch.object(llm.requests, "get", return_value=_Reply()):
            result = llm.test_server("https://example.com/v1", "k")
        self.assertEqual(result["error"], "Server responded with HTTP 400")


class VertexConnectionTests(unittest.TestCase):
    """Vertex has no model list: the connection test asks a free question
    and lets the callers accept the model name as typed ("any_model")."""

    def _probe(self, status):
        seen = {}

        class _Reply:
            status_code = status
            text = ""

        def fake_get(url, headers=None, timeout=None):
            seen["url"] = url
            return _Reply()

        with patch.object(llm.requests, "get", fake_get):
            return llm.test_server(vertex_auth.base_url("p", "global"), "pass"), seen["url"]

    def test_working_pass(self):
        result, url = self._probe(200)
        self.assertEqual(url, "https://aiplatform.googleapis.com/v1/projects/p/locations/global/endpoints")
        self.assertTrue(result["reachable"])
        self.assertTrue(result["any_model"])
        self.assertIsNone(result["error"])

    def test_refused_pass(self):
        result, _url = self._probe(401)
        self.assertFalse(result.get("any_model"))
        self.assertIn("save the JSON key again", result["error"])


# ---------- the Settings "Test" button (providers.test_model) --------------

class _FakePack:
    """Stands in for a structured reply (anything with her two lines)."""

    def __init__(self, jps="", eng=""):
        self.assistant_reply_JPS = jps
        self.assistant_reply_ENG = eng


class _FakePlain:
    """A plain (non-structured) reply."""

    def __init__(self, content=""):
        self.content = content


class _StatusError(Exception):
    """An API error with an HTTP status (like openai.APIStatusError)."""

    def __init__(self, status, text=""):
        super().__init__(text or f"HTTP {status}")
        self.status_code = status


class APIConnectionError(Exception):
    """A connection-level failure: the class name is what is read."""


class _BodyStatusError(_StatusError):
    """An API error that keeps the provider's parsed body (the openai SDK
    errors do: .body = {"error": {"message": ...}}, {"message": ...} or a
    list of such objects)."""

    def __init__(self, status, body=None, text=None):
        if text is None:
            text = f"Error code: {status} - {body!r}"
        super().__init__(status, text)
        self.body = body


class _FakeBound:
    def __init__(self, run):
        self._run = run

    def invoke(self, messages):
        return self._run(messages)

    def bind(self, **kwargs):
        return self


class _FakeClient:
    """The shape llm.client_for returns: both structured paths (cloud
    with_structured_output, local bind_tools) and the plain call. No
    network - the scenarios are scripted per call kind."""

    def __init__(self, base_url="https://api.groq.com/openai/v1",
                 structured=None, structured_exc=None,
                 plain=None, plain_exc=None):
        self.openai_api_base = base_url
        self.base_url = base_url
        self.api_key = ""
        self._structured, self._structured_exc = structured, structured_exc
        self._plain, self._plain_exc = plain, plain_exc
        self.calls = []

    def _run(self, which, messages):
        self.calls.append(which)
        if which == "structured":
            exc, value = self._structured_exc, self._structured
        else:
            exc, value = self._plain_exc, self._plain
        if exc is not None:
            raise exc
        return value

    def with_structured_output(self, schema, method=None):
        return _FakeBound(lambda messages: self._run("structured", messages))

    def bind_tools(self, tools, tool_choice=None):
        return _FakeBound(lambda messages: self._run("structured", messages))

    def invoke(self, messages):
        return self._run("plain", messages)


class TestModelTests(_TempData):
    """The Settings "Test" button: one message in Amadeus's own reply
    format, every outcome in the right words, no key anywhere in the
    reply, and nothing saved touched. All calls are faked - no network."""

    CLOUD = "groq"

    def _set_cloud(self, key="gsk-test"):
        memory.save_llm_server("https://api.groq.com/openai/v1")
        if key:
            memory.save_api_key(key)

    def _run(self, fake, pid=None, address="", model="gpt-test", patches=()):
        import contextlib
        with contextlib.ExitStack() as stack:
            build = stack.enter_context(
                patch.object(providers.llm, "client_for", return_value=fake))
            for p in patches:
                stack.enter_context(p)
            return providers.test_model(pid or self.CLOUD, address, model), build

    def test_ok_reports_the_model_and_the_seconds(self):
        self._set_cloud()
        fake = _FakeClient(structured=_FakePack("やあ。", "Hello."))
        result, build = self._run(fake, model="llama-x")
        build.assert_called_once()
        args, _ = build.call_args
        # the client it asked for: the table address, the saved key, the model
        self.assertEqual(args[0], "https://api.groq.com/openai/v1")
        self.assertEqual(args[1], "gsk-test")
        self.assertEqual(args[2], "llama-x")
        self.assertTrue(result["ok"])
        self.assertEqual(result["outcome"], "ok")
        self.assertEqual(result["model"], "llama-x")
        self.assertRegex(result["message"],
                         r"^Works with Amadeus\. llama-x replied in \d+\.\d s\.$")
        self.assertIsInstance(result["seconds"], (int, float))
        self.assertLessEqual(result["seconds"], 5)

    def test_no_model_asks_for_a_model(self):
        self._set_cloud()
        fake = _FakeClient()
        result, build = self._run(fake, model="")
        self.assertEqual(result["outcome"], "no_model")
        self.assertEqual(result["message"], "Pick a model first.")
        build.assert_not_called()

    def test_cloud_without_a_saved_key_still_sends_the_test(self):
        # No early refusal anymore: the client sends a placeholder key, so a
        # model that answers works even before any key is saved.
        memory.save_llm_server("https://api.groq.com/openai/v1")  # no key
        fake = _FakeClient(structured=_FakePack("やあ。", "Hello."))
        result, build = self._run(fake)
        build.assert_called_once()
        args, _ = build.call_args
        self.assertEqual(args[0], "https://api.groq.com/openai/v1")
        self.assertEqual(args[1], "")  # the placeholder lives inside client_for
        self.assertTrue(result["ok"])

    def test_cloud_without_a_saved_key_refusal_names_the_fix(self):
        # A refusal with no key saved says so: the fix is to save a key,
        # not to correct one (outcome stays bad_key).
        memory.save_llm_server("https://api.groq.com/openai/v1")  # no key
        fake = _FakeClient(structured_exc=_StatusError(401, "Invalid API key"),
                           plain_exc=_StatusError(403, "permission denied"))
        result, build = self._run(fake)
        self.assertEqual(result["outcome"], "bad_key")
        self.assertEqual(result["message"],
                         "The provider rejected the request - no key is saved yet.")

    def test_local_needs_no_key(self):
        memory.save_llm_server("http://localhost:11434/v1")
        fake = _FakeClient(structured=_FakePack("やあ。", "Hello."))
        result, build = self._run(fake, pid="ollama")
        self.assertTrue(result["ok"])
        args, _ = build.call_args
        self.assertEqual(args[0], "http://localhost:11434/v1")
        self.assertEqual(args[1], "")  # the placeholder lives inside client_for

    def test_vertex_without_a_json_key(self):
        fake = _FakeClient()
        result, build = self._run(
            fake, pid="vertex",
            patches=(patch.object(providers.vertex_auth, "info",
                                  return_value={"configured": False, "project": "",
                                                "region": "global", "address": ""}),))
        self.assertEqual(result["outcome"], "no_key")
        self.assertEqual(result["message"], "No JSON key saved yet.")
        build.assert_not_called()

    def test_vertex_sign_in_refused_shows_googles_words(self):
        fake = _FakeClient()
        words = "Google refused the JSON key (HTTP 400)."
        result, build = self._run(
            fake, pid="vertex",
            patches=(
                patch.object(providers.vertex_auth, "info",
                             return_value={"configured": True, "project": "p",
                                           "region": "global", "address": ""}),
                patch.object(providers.vertex_auth, "token",
                             side_effect=vertex_auth.VertexSignInError(words))))
        self.assertEqual(result["outcome"], "bad_key")
        self.assertEqual(result["message"], words)
        build.assert_not_called()

    def test_vertex_model_gets_the_google_prefix_and_project_address(self):
        fake = _FakeClient(structured=_FakePack("やあ。", "Hello."))
        result, build = self._run(
            fake, pid="vertex", model="gemini-3.8-flash",
            patches=(
                patch.object(providers.vertex_auth, "info",
                             return_value={"configured": True, "project": "my-project",
                                           "region": "global", "address": ""}),
                patch.object(providers.vertex_auth, "token", return_value="ya29-pass")))
        self.assertTrue(result["ok"])
        self.assertEqual(result["model"], "google/gemini-3.8-flash")
        args, _ = build.call_args
        self.assertIn("aiplatform.googleapis.com", args[0])
        self.assertIn("projects/my-project", args[0])
        self.assertEqual(args[2], "google/gemini-3.8-flash")

    def test_bad_key_401_never_leaks_the_key(self):
        key = "sk-SECRET-1234567890"
        self._set_cloud(key=key)
        fake = _FakeClient(structured_exc=_StatusError(401, f"Invalid API key provided: {key}"))
        result, _ = self._run(fake)
        self.assertEqual(result["outcome"], "bad_key")
        self.assertEqual(result["message"], "The provider rejected this key.")
        self.assertNotIn(key, json.dumps(result))
        self.assertNotIn("SECRET", json.dumps(result))

    def test_bad_key_403(self):
        self._set_cloud()
        fake = _FakeClient(structured_exc=_StatusError(403, "permission denied"))
        result, _ = self._run(fake)
        self.assertEqual(result["outcome"], "bad_key")

    def test_bad_key_http_400_about_the_api_key(self):
        self._set_cloud()
        fake = _FakeClient(structured_exc=_StatusError(400, "Please pass a valid API key"))
        result, _ = self._run(fake)
        self.assertEqual(result["outcome"], "bad_key")

    def test_model_missing_404(self):
        self._set_cloud()
        fake = _FakeClient(structured_exc=_StatusError(404, "model not found"))
        result, _ = self._run(fake, model="ghost-model")
        self.assertEqual(result["outcome"], "model_missing")
        self.assertEqual(result["message"], "The provider does not know the model ghost-model.")

    def test_model_missing_http_400(self):
        self._set_cloud()
        fake = _FakeClient(structured_exc=_StatusError(400, "The model 'ghost' does not exist."))
        result, _ = self._run(fake, model="ghost")
        self.assertEqual(result["outcome"], "model_missing")

    def test_no_credit_402(self):
        self._set_cloud()
        fake = _FakeClient(structured_exc=_StatusError(402, "insufficient credits"))
        result, _ = self._run(fake)
        self.assertEqual(result["outcome"], "no_credit")
        self.assertEqual(result["message"],
                         "The provider says this account has no credit or quota left.")

    def test_no_credit_429_billing_wording(self):
        self._set_cloud()
        fake = _FakeClient(structured_exc=_StatusError(429, "Billing hard limit exceeded"))
        result, _ = self._run(fake)
        self.assertEqual(result["outcome"], "no_credit")

    def test_no_credit_429_daily_quota(self):
        self._set_cloud()
        fake = _FakeClient(structured_exc=_StatusError(429, "You have exceeded your daily quota"))
        result, _ = self._run(fake)
        self.assertEqual(result["outcome"], "no_credit")

    def test_rate_limited_429(self):
        self._set_cloud()
        fake = _FakeClient(structured_exc=_StatusError(429, "Too many requests"))
        result, _ = self._run(fake)
        self.assertEqual(result["outcome"], "rate_limited")
        self.assertEqual(result["message"],
                         "The provider is rate-limiting right now. Try again in a minute.")

    def test_wrong_format_cloud(self):
        # The structured call came back empty; the plain call does answer.
        self._set_cloud()
        fake = _FakeClient(structured=None, plain=_FakePlain("Hello!"))
        result, _ = self._run(fake)
        self.assertEqual(result["outcome"], "wrong_format")
        self.assertEqual(result["message"],
                         "The model answers, but not in the form Amadeus needs.")
        self.assertEqual(fake.calls, ["structured", "plain"])

    def test_wrong_format_local_server(self):
        # Local servers take the tool_choice="required" branch: the answer
        # carries no usable tool call, but the plain call answers.
        memory.save_llm_server("http://localhost:11434/v1")
        fake = _FakeClient(base_url="http://localhost:11434/v1",
                           structured=_FakePlain("hi"), plain=_FakePlain("hi there"))
        result, _ = self._run(fake, pid="ollama")
        self.assertEqual(result["outcome"], "wrong_format")

    def test_unreachable_after_a_network_failure(self):
        self._set_cloud()
        fake = _FakeClient(structured_exc=APIConnectionError("Connection refused"))
        result, _ = self._run(fake)
        self.assertEqual(result["outcome"], "unreachable")
        self.assertEqual(result["message"], "Could not reach the server.")
        self.assertEqual(fake.calls, ["structured"])  # no plain retry after this

    def test_unreachable_for_the_openai_sdk_errors(self):
        # The openai SDK's own connection/timeout errors (they carry no
        # status_code): a closed port must read "Could not reach the
        # server.", not "The test failed: Connection error."
        import httpx
        import openai
        self._set_cloud()
        request = httpx.Request("POST", "http://localhost:19999/v1/chat/completions")
        for error in (openai.APIConnectionError(request=request),
                      openai.APITimeoutError(request=request)):
            fake = _FakeClient(structured_exc=error)
            result, _ = self._run(fake)
            self.assertEqual(result["outcome"], "unreachable", type(error).__name__)
            self.assertEqual(result["message"], "Could not reach the server.")
            self.assertEqual(fake.calls, ["structured"])

    def test_unreachable_for_a_wrapped_network_error(self):
        # A wrapper exception (a framework class, or a renamed one) still
        # counts: the openai error is found through __cause__ and
        # __context__.
        import httpx
        import openai
        self._set_cloud()
        request = httpx.Request("POST", "http://localhost:19999/v1/chat/completions")
        try:
            raise openai.APIConnectionError(request=request)
        except Exception as inner:
            wrapper = RuntimeError("wrapped by the framework")
            wrapper.__cause__ = inner
        fake = _FakeClient(structured_exc=wrapper)
        result, _ = self._run(fake)
        self.assertEqual(result["outcome"], "unreachable")
        # A __context__-only chain: the wrapper is RAISED inside the handler
        # (only a raised exception gets its __context__ set, __cause__ None).
        try:
            try:
                raise openai.APITimeoutError(request=request)
            except Exception:
                raise RuntimeError("chained")
        except RuntimeError as chained:
            fake = _FakeClient(structured_exc=chained)
            result, _ = self._run(fake)
            self.assertEqual(result["outcome"], "unreachable")

    def test_test_client_never_retries_and_connects_fast(self):
        # The one-off client the page asks for: zero SDK retries (even with
        # a reasoning level that gives the real clients one) and a ~5 s
        # connect ceiling, so a dead server cannot hold the test for ~30 s.
        for level in ("off", "high"):
            client = llm.client_for("http://localhost:19999/v1", "k", "m",
                                    reasoning=level)
            root = client.root_client
            self.assertEqual(root.max_retries, 0, level)
            connect = root._client.timeout
            while not isinstance(connect, (int, float)):
                connect = getattr(connect, "connect", None)
                if connect is None:
                    break
            self.assertIsNotNone(connect, level)
            self.assertLessEqual(connect, 5.5, level)

    def test_auto_uses_autodetect_like_the_probe(self):
        # "auto" asks autodetect_url (the first live local port) - not a
        # "no server address" error like a table row without a URL.
        fake = _FakeClient(structured=_FakePack("やあ。", "Hello."))
        result, build = self._run(
            fake, pid="auto",
            patches=(patch.object(providers.llm, "autodetect_url",
                                  return_value="http://localhost:8888/v1"),))
        self.assertTrue(result["ok"])
        args, _ = build.call_args
        self.assertEqual(args[0], "http://localhost:8888/v1")

    def test_unknown_model_http_400_openrouter_shape(self):
        # OpenRouter answers an unknown model with HTTP 400 and a body that
        # also carries the caller's account id: it must read as "unknown
        # model", never show the raw body.
        self._set_cloud()
        body = {"error": {"message": "no-such/model-xyz is not a valid model ID",
                          "code": 400}, "user_id": "user_123"}
        fake = _FakeClient(structured_exc=_BodyStatusError(400, body=body))
        result, _ = self._run(fake, model="no-such/model-xyz")
        self.assertEqual(result["outcome"], "model_missing")
        self.assertEqual(result["message"],
                         "The provider does not know the model no-such/model-xyz.")
        self.assertNotIn("user_123", result["message"])

    def test_unknown_model_phrases_read_as_model_missing(self):
        # Every phrase a provider uses to say "we do not know that model",
        # on the 400/422 answers; 404 stays model_missing whatever its text.
        self._set_cloud()
        phrases = (
            "no-such/model-xyz is not a valid model ID",
            "Invalid model 'gpt-x' for this endpoint",
            "Model not found: gpt-x",
            "model_not_found",
            "The model 'gpt-x' does not exist.",
            "No such model: gpt-x",
            "Unknown model 'gpt-x'",
            "The model gpt-x is not supported.",
        )
        for status in (400, 422):
            for phrase in phrases:
                fake = _FakeClient(structured_exc=_StatusError(status, phrase))
                result, _ = self._run(fake, model="gpt-x")
                self.assertEqual(result["outcome"], "model_missing",
                                 f"{status}: {phrase!r}")
        fake = _FakeClient(structured_exc=_StatusError(404, "whatever"))
        result, _ = self._run(fake, model="gpt-x")
        self.assertEqual(result["outcome"], "model_missing")

    def test_error_outcome_shows_the_providers_own_sentence(self):
        # The "error" outcome shows just the provider's sentence from the
        # error body (dict, message-only and list shapes) - never the raw
        # body with its ids.
        self._set_cloud()
        shapes = (
            ({"error": {"message": "Something went wrong on our side", "code": 500},
              "user_id": "user_1"}, "Something went wrong on our side"),
            ({"message": "Something went wrong on our side"},
             "Something went wrong on our side"),
            ([{"error": {"message": "Something went wrong on our side"}}],
             "Something went wrong on our side"),
        )
        for body, sentence in shapes:
            fake = _FakeClient(structured_exc=_BodyStatusError(500, body=body))
            result, _ = self._run(fake)
            self.assertEqual(result["outcome"], "error", repr(body))
            self.assertEqual(result["message"], f"The test failed: {sentence}",
                             repr(body))
            self.assertNotIn("user_1", result["message"])

    def test_error_reason_from_the_text_message_field(self):
        # No body kept (older SDKs): the first 'message': '...' out of the
        # error text is the provider's sentence; the ids around it stay out.
        self._set_cloud()
        text = ("Error code: 400 - {'error': {'message': 'We could not process that', "
                "'code': 400}, 'user_id': 'user_9', 'request_id': 'req_7'}")
        fake = _FakeClient(structured_exc=_StatusError(400, text))
        result, _ = self._run(fake)
        self.assertEqual(result["outcome"], "error")
        self.assertEqual(result["message"], "The test failed: We could not process that")

    def test_error_reason_never_carries_ids_keys_or_bearers(self):
        self._set_cloud()
        body = {"error": {"message":
                          "Refused for user_abc123 (org-42) request req_55 "
                          "Bearer sk-supersecret123456"}}
        fake = _FakeClient(structured_exc=_BodyStatusError(500, body=body))
        result, _ = self._run(fake)
        self.assertEqual(result["message"],
                         "The test failed: Refused for <id> (<id>) request <id> "
                         "Bearer <key>")

    def test_error_reason_caps_at_120_chars(self):
        self._set_cloud()
        fake = _FakeClient(structured_exc=_StatusError(500, ("word " * 40).strip()))
        result, _ = self._run(fake)
        self.assertEqual(result["outcome"], "error")
        reason = result["message"][len("The test failed: "):]
        self.assertLessEqual(len(reason), 120)

    def test_local_wording_says_server_not_provider(self):
        # Local rows are "the server" (cloud and Vertex keep "the
        # provider"): the unknown-model and rejected-key words both.
        memory.save_llm_server("http://localhost:11434/v1")
        fake = _FakeClient(structured_exc=_StatusError(404, "model not found"))
        result, _ = self._run(fake, pid="ollama", model="ghost")
        self.assertEqual(result["outcome"], "model_missing")
        self.assertEqual(result["message"], "The server does not know the model ghost.")
        # No key saved: a refusal names that (the client sent a placeholder).
        fake = _FakeClient(structured_exc=_StatusError(401, "invalid key"))
        result, _ = self._run(fake, pid="ollama")
        self.assertEqual(result["outcome"], "bad_key")
        self.assertEqual(result["message"],
                         "The server rejected the request - no key is saved yet.")
        # With a key saved the words go back to the plain rejection.
        memory.save_api_key("local-key")
        fake = _FakeClient(structured_exc=_StatusError(401, "invalid key"))
        result, _ = self._run(fake, pid="ollama")
        self.assertEqual(result["outcome"], "bad_key")
        self.assertEqual(result["message"], "The server rejected this key.")

    def test_custom_wording_says_server_too(self):
        # A custom address is usually someone's own server.
        memory.save_llm_server("http://myown:8000/v1")
        fake = _FakeClient(structured_exc=_StatusError(404, "model 'ghost' does not exist"))
        result, _ = self._run(fake, pid="custom",
                              address="http://myown:8000/v1", model="ghost")
        self.assertEqual(result["message"], "The server does not know the model ghost.")

    def test_vertex_wording_stays_provider(self):
        fake = _FakeClient(structured_exc=_StatusError(403, "forbidden"))
        result, _ = self._run(
            fake, pid="vertex",
            patches=(
                patch.object(providers.vertex_auth, "info",
                             return_value={"configured": True, "project": "p",
                                           "region": "global", "address": ""}),
                patch.object(providers.vertex_auth, "token", return_value="ya29-pass")))
        self.assertEqual(result["outcome"], "bad_key")
        self.assertEqual(result["message"], "The provider rejected this key.")

    def test_error_keeps_a_short_provider_reason(self):
        self._set_cloud()
        fake = _FakeClient(structured_exc=_StatusError(500, "Internal server error at the provider"))
        result, _ = self._run(fake)
        self.assertEqual(result["outcome"], "error")
        self.assertEqual(result["message"],
                         "The test failed: Internal server error at the provider")

    def test_error_when_the_model_does_not_answer_at_all(self):
        self._set_cloud()
        fake = _FakeClient(structured=None, plain=_FakePlain("   "))
        result, _ = self._run(fake)
        self.assertEqual(result["outcome"], "error")
        self.assertEqual(result["message"], "The test failed: the model did not answer.")

    def test_custom_without_an_address(self):
        fake = _FakeClient()
        result, build = self._run(fake, pid="custom", address="")
        self.assertEqual(result["outcome"], "error")
        self.assertIn("no server address", result["message"])
        build.assert_not_called()

    def test_unknown_provider_raises(self):
        with self.assertRaisesRegex(ValueError, "Unknown provider"):
            providers.test_model("nope", "", "m")

    def test_the_test_changes_nothing_saved(self):
        import chat
        self._set_cloud("gsk-live")
        memory.save_llm_model("gpt-live")
        (self.data / "llm_provider.txt").write_text(self.CLOUD, encoding="utf-8")
        keys_before = providers.saved_keys()
        cache_before = dict(providers.llm._LLM_CACHE)
        url_before = dict(providers.llm._LLM_CACHE_URL)
        api_key_before = chat.API_KEY
        model_before = chat.LLM_Model
        fake = _FakeClient(structured=_FakePack("やあ。", "Hello."))
        result, _ = self._run(fake)
        self.assertTrue(result["ok"])
        self.assertEqual(providers.saved_keys(), keys_before)
        self.assertEqual(memory.load_api_key(), "gsk-live")
        self.assertEqual(memory.load_llm_model(), "gpt-live")
        self.assertEqual((self.data / "llm_provider.txt").read_text(encoding="utf-8"), self.CLOUD)
        # the test client never enters the app's client cache
        self.assertEqual(dict(providers.llm._LLM_CACHE), cache_before)
        self.assertEqual(dict(providers.llm._LLM_CACHE_URL), url_before)
        self.assertEqual(chat.API_KEY, api_key_before)
        self.assertEqual(chat.LLM_Model, model_before)


class NetworkErrorDetectionTests(unittest.TestCase):
    """The "the server never answered at all" check: the openai SDK's own
    classes, a wrapper exception (via __cause__ / __context__), a renamed
    class, and the plain errors that must NOT count as unreachable."""

    def test_openai_connection_and_timeout_errors(self):
        import httpx
        import openai
        request = httpx.Request("POST", "http://localhost:19999/v1/chat/completions")
        self.assertTrue(providers._is_network_error(
            openai.APIConnectionError(request=request)))
        self.assertTrue(providers._is_network_error(
            openai.APITimeoutError(request=request)))

    def test_a_wrapper_is_followed_through_cause_and_context(self):
        import httpx
        import openai
        request = httpx.Request("POST", "http://localhost:19999/v1/chat/completions")
        try:
            raise openai.APIConnectionError(request=request)
        except Exception as inner:
            wrapper = RuntimeError("wrapped by the framework")
            wrapper.__cause__ = inner
        self.assertTrue(providers._is_network_error(wrapper))
        # A __context__-only chain: the wrapper is RAISED inside the handler
        # (only a raised exception gets its __context__ set, __cause__ None).
        try:
            try:
                raise openai.APITimeoutError(request=request)
            except Exception:
                raise RuntimeError("chained")
        except RuntimeError as chained:
            self.assertTrue(providers._is_network_error(chained))

    def test_a_renamed_class_keeps_its_known_ancestors(self):
        class RenamedTimeout(TimeoutError):
            pass
        self.assertTrue(providers._is_network_error(RenamedTimeout("timed out")))

    def test_plain_errors_are_not_network_failures(self):
        self.assertFalse(providers._is_network_error(ValueError("bad json")))
        self.assertFalse(providers._is_network_error(_StatusError(500, "boom")))
        self.assertFalse(providers._is_network_error(_StatusError(400, "weird 400")))


class KeyCheckedTests(_TempData):
    """key_checked on the probe reply: True only when the model list really
    needed the key (a bogus key is refused by the same /models endpoint);
    has_key rides along for the page's words."""

    OK = {"reachable": True, "models": ["m1"], "loading": False, "error": None}
    REFUSED = {"reachable": True, "models": [], "loading": False,
               "error": "Server is running but rejected the API key (HTTP 401)"}
    LISTED = {"reachable": True, "models": ["a", "b"], "loading": False, "error": None}
    DOWN = {"reachable": False, "models": [], "loading": False, "error": "connection refused"}
    EMPTY = {"reachable": True, "models": [], "loading": False, "error": None}

    def _probe_cloud(self, first, second=None, key="sk-test"):
        memory.save_llm_server("https://api.deepseek.com")
        if key:
            memory.save_api_key(key)
        with patch.object(llm, "test_server",
                          side_effect=[dict(first)] + ([dict(second)] if second else [])) as probe:
            return providers.probe("deepseek"), probe

    def test_refused_bogus_key_proves_the_real_one(self):
        result, probe = self._probe_cloud(self.OK, self.REFUSED)
        self.assertTrue(result["key_checked"])
        self.assertTrue(result["has_key"])
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(probe.call_args_list[1][0],
                         ("https://api.deepseek.com", "invalid-key-check"))

    def test_a_listing_without_a_key_proves_nothing(self):
        result, _ = self._probe_cloud(self.LISTED, self.LISTED)
        self.assertFalse(result["key_checked"])

    def test_no_extra_call_when_the_listing_failed(self):
        result, probe = self._probe_cloud(self.DOWN)
        self.assertFalse(result["key_checked"])
        self.assertEqual(probe.call_count, 1)

    def test_a_rejected_key_is_not_checked(self):
        # A listing that itself FAILED (the server answered with a refusal)
        # proves nothing, whatever the bogus-key follow-up would say - and
        # the follow-up is not even made.
        result, probe = self._probe_cloud(self.REFUSED, self.REFUSED)
        self.assertFalse(result["key_checked"])
        self.assertEqual(probe.call_count, 1)

    def test_a_successful_empty_listing_proves_nothing(self):
        # No models at all = a failed listing: there was no key to prove.
        result, probe = self._probe_cloud(self.EMPTY, self.REFUSED)
        self.assertFalse(result["key_checked"])
        self.assertEqual(probe.call_count, 1)

    def test_no_extra_call_without_a_saved_key(self):
        memory.save_llm_server("https://api.deepseek.com")  # no key at all
        with patch.object(llm, "test_server", return_value=dict(self.OK)) as probe:
            result = providers.probe("deepseek")
        self.assertFalse(result["key_checked"])
        self.assertFalse(result["has_key"])
        self.assertEqual(probe.call_count, 1)

    def test_local_is_never_key_checked(self):
        memory.save_llm_server("http://localhost:11434/v1")
        with patch.object(llm, "test_server", return_value=dict(self.OK)) as probe:
            result = providers.probe("ollama")
        self.assertFalse(result["key_checked"])
        self.assertEqual(probe.call_count, 1)

    def test_vertex_is_never_key_checked(self):
        n, e, d = rsa_numbers()
        with patch.object(vertex_auth, "_rsa_numbers", return_value=(n, e, d)), \
             patch.object(vertex_auth, "check_ready", return_value={"ok": True, "error": None}):
            vertex_auth.save_key(fake_vertex_key(), "global")
            result = providers.probe("vertex")
        self.assertFalse(result["key_checked"])
        self.assertTrue(result["has_key"])
        self.assertTrue(result["reachable"])


if __name__ == "__main__":
    unittest.main()
