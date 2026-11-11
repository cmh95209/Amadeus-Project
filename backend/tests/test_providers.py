# -*- coding: utf-8 -*-
"""Offline checks for the LLM provider list (feature/provider-picker):
  1. the table itself (unique ids, cloud URLs https, no trailing slash);
  2. which provider a saved server address belongs to (blank, table URLs,
     trailing slash/case, the old Gemini endpoint, Vertex, custom);
  3. how existing installs map onto the table (address + api_key.txt, no
     provider file) and which key each provider gets;
  4. switching providers: the old key is stashed per provider, the new
     one goes into use ("" clears it), custom needs an address, Vertex
     needs a saved JSON key and signs in for its pass;
  5. probes: Vertex via check_ready, everyone else via llm.test_server
     (stubbed - no network, no real keys);
  6. the background renewal: only while Vertex is active, only when due
     or forced, a failed sign-in never kills anything;
  7. the routes on the real Flask app (temp data folder): listing, saving
     keys, model probes, key reveal (this PC / own Tailscale tunnel /
     refused for phones and public funnels), saving the Vertex key, and
     switching providers (a Vertex model is stored as google/<name>);
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
                           ("PATH_TO_LLM_SERVER", str(d / "llm_server.txt"))):
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
        with patch.object(llm, "test_server", return_value=dict(self.OK)) as probe:
            result = providers.probe("deepseek")
        self.assertEqual(result["models"], ["m1"])
        probe.assert_called_once_with("https://api.deepseek.com", "sk-ds")

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
    stubbed - no network). providers.py imported the ORIGINAL memory module
    at collection time, so point it - and llm_server.txt - at this test's
    folder first."""

    def setUp(self):
        super().setUp()
        p = patch.object(providers, "memory", self.memory)
        p.start()
        self.addCleanup(p.stop)
        s = patch.object(self.memory, "PATH_TO_LLM_SERVER",
                         str(Path(self.directory.name) / "llm_server.txt"))
        s.start()
        self.addCleanup(s.stop)
        vertex_auth.forget_token()
        self.addCleanup(vertex_auth.forget_token)

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
        self.assertEqual(data["vertex"]["region"], vertex_auth.DEFAULT_REGION)
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

    def test_save_key_rejections(self):
        self.assertEqual(self.client.post("/llm/key", json={"provider": "nope", "key": "x"}).status_code, 400)
        self.assertEqual(self.client.post("/llm/key", json={"provider": "vertex", "key": "x"}).status_code, 400)
        self.assertEqual(self.client.post("/llm/key", json={"provider": "groq", "key": "   "}).status_code, 400)
        self.assertEqual(self.client.post("/llm/key", json={"provider": "groq"}).status_code, 400)

    def test_save_key_for_the_active_provider_applies_it(self):
        res = self.client.post("/llm/provider", json={"id": "openrouter"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json["active"], "openrouter")
        res = self.client.post("/llm/key", json={"provider": "openrouter", "key": "sk-new"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.memory.load_api_key(), "sk-new")
        self.assertEqual(self.chat.API_KEY, "sk-new")

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
        n, e, d = rsa_numbers()
        with patch.object(vertex_auth, "_rsa_numbers", return_value=(n, e, d)), \
             patch.object(vertex_auth, "check_ready", return_value={"ok": True, "error": None}):
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
        self.assertEqual(body["models"], providers.VERTEX_MODELS)

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
        with patch.object(vertex_auth, "_rsa_numbers", return_value=(n, e, d)), \
             patch.object(vertex_auth, "token", return_value="ya29-offline-pass") as token:
            vertex_auth.save_key(fake_vertex_key(), "global")
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


if __name__ == "__main__":
    unittest.main()
