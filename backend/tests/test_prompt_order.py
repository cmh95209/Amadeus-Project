# -*- coding: utf-8 -*-
"""Offline checks for prompt order A (2026-10-22).

A local server (and cloud prompt caching) reuses a prompt only up to the first
changed byte, so her prompt must START with the blocks that never change:
  1. the voice rules carry no trust / tier / address lines (those change);
     the closeness block carries them;
  2. two replies with different memories, time, trust and history share the
     whole fixed start (personality, voice rules, reply rules, web rules), and
     the changing blocks all come after it - on the normal and the
     voice-first reply paths;
  3. order B (built-in model): the changing blocks leave the instruction
     message and become one "right now" note after the newest user
     message (after it: the server resumes from the start of that
     message), so the instruction message is identical across replies.
All LLM calls and the stores are stubbed; no network, no live data.
"""
import contextlib
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import attachments
import cm_bridge
import chat
import memory as store
import stats
import ja_voice

_PERSONALITY = {"role": "system", "content": "PERSONALITY"}


class _FakeStructured:
    def __init__(self, sink):
        self._sink = sink

    def invoke(self, messages):
        self._sink["messages"] = messages
        return chat.AmadeusPack(assistant_reply_JPS="こんにちは",
                                assistant_reply_ENG="Hello.")


class _FakeLLM:
    def __init__(self, sink):
        self._sink = sink

    def with_structured_output(self, schema, method=None):
        return _FakeStructured(self._sink)


def _voice_patches():
    return [mock.patch.object(ja_voice, "load_voice_block", lambda: "VOICE RULES"),
            mock.patch.object(ja_voice, "address_rule", lambda tier: "ADDRESS " + tier)]


class VoiceSplitTests(unittest.TestCase):
    def test_rules_fixed_closeness_changes(self):
        with contextlib.ExitStack() as stack:
            for p in _voice_patches():
                stack.enter_context(p)
            rules = ja_voice.build_voice_rules()["content"]
            low = ja_voice.build_closeness_context(10)["content"]
            high = ja_voice.build_closeness_context(90)["content"]
        self.assertNotIn("trust", rules.split("FOREIGN WORDS")[0])
        self.assertNotIn("ADDRESS", rules)
        self.assertTrue(rules.endswith("VOICE RULES"))
        self.assertIn("trust 10 out of 100", low)
        self.assertIn("trust 90 out of 100", high)
        self.assertIn("ADDRESS", low)
        combined = ja_voice.build_voice_context  # kept: closeness + rules in one
        with contextlib.ExitStack() as stack:
            for p in _voice_patches():
                stack.enter_context(p)
            self.assertEqual(combined(10)["content"], low + "\n\n" + rules)


class PromptOrderTests(unittest.TestCase):
    right_now = False

    def _run(self, fn, trust, memory_text, timing, history):
        sink = {}
        with contextlib.ExitStack() as stack:
            for p in _voice_patches():
                stack.enter_context(p)
            for target, name, value in [
                (chat, "get_llm", lambda *a, **k: _FakeLLM(sink)),
                (store, "load_web_access", lambda: False),
                (store, "load_character_book_messages", lambda _last: []),
                (store, "load_default_personality_messages", lambda: [dict(_PERSONALITY)]),
                (store, "load_active_conversation", lambda: 1),
                (stats, "load_stat", lambda _name: trust),
                (chat, "_finalize", lambda p, llm: p),
                (chat, "_web_off_honesty_net", lambda p: p),
                (chat, "_right_now_note", lambda: self.right_now),
                # Cross-tab blocks: their placement has its own tests below.
                (chat.cross_tab, "catch_up_block", lambda *a, **k: None),
                (chat.cross_tab, "search_block", lambda *a, **k: None),
                (cm_bridge, "fetch_memory_block",
                 lambda _cid: {"role": "system", "content": memory_text}),
                # A made-up chat: no pictures from whatever the data folder holds.
                (attachments, "with_pictures", lambda msgs, *a, **k: msgs),
                (attachments, "picture_index_message", lambda *a, **k: None),
            ]:
                stack.enter_context(mock.patch.object(target, name, value))
            fn(list(history), internal_context={"role": "system", "content": timing})
        return sink["messages"]

    def _two_turns(self, fn):
        one = self._run(fn, 20, "MEMORY tea", "TIMING 10:00",
                        [{"role": "user", "content": "hi"}])
        two = self._run(fn, 80, "MEMORY rain", "TIMING 11:30",
                        [{"role": "user", "content": "hi"},
                         {"role": "assistant", "content": "やあ"},
                         {"role": "user", "content": "how are you?"}])
        return one, two

    def _fixed(self, rules):
        with contextlib.ExitStack() as stack:
            for p in _voice_patches():
                stack.enter_context(p)
            return "\n\n".join(["PERSONALITY", ja_voice.build_voice_rules()["content"],
                                rules["content"], chat.NO_WEB_BLOCK["content"]])

    def _check(self, fn, rules):
        one, two = self._two_turns(fn)
        fixed = self._fixed(rules)
        for msgs, memory_text, timing in [(one, "MEMORY tea", "TIMING 10:00"),
                                          (two, "MEMORY rain", "TIMING 11:30")]:
            system = msgs[0]["content"]
            self.assertEqual([m["role"] for m in msgs].count("system"), 1)
            self.assertTrue(system.startswith(fixed + "\n\nCLOSENESS"))
            rest = system[len(fixed):]
            self.assertLess(rest.index("CLOSENESS"), rest.index(memory_text))
            self.assertLess(rest.index(memory_text), rest.index(timing))
            self.assertTrue(system.endswith(timing))

    def test_normal_reply(self):
        self._check(chat.getResponsePacked, chat._PACK_RULES)

    def test_voice_first_reply(self):
        self._check(chat.getResponsePackedVoiceFirst, chat._PACK_RULES_JA_ONLY)


class RightNowNoteTests(PromptOrderTests):
    right_now = True

    def _check(self, fn, rules):
        one, two = self._two_turns(fn)
        fixed = self._fixed(rules)
        self.assertEqual(one[0], two[0])  # the instruction message never changes
        for msgs, memory_text, timing in [(one, "MEMORY tea", "TIMING 10:00"),
                                          (two, "MEMORY rain", "TIMING 11:30")]:
            self.assertEqual(msgs[0]["content"], fixed)
            last, note = msgs[-2], msgs[-1]
            self.assertEqual((last["role"], note["role"]), ("user", "system"))
            self.assertTrue(note["content"].startswith(chat._RIGHT_NOW_LABEL + "CLOSENESS"))
            body = note["content"]
            self.assertLess(body.index(memory_text), body.index(timing))
            self.assertTrue(body.endswith(timing))
        self.assertEqual([m["content"] for m in two[1:-1]], ["hi", "やあ", "how are you?"])

    def test_no_user_line_puts_the_note_last(self):
        msgs = chat._ordered_prompt({"role": "system", "content": "R"},
                                    {"role": "system", "content": "W"}, [], [], right_now=True)
        self.assertTrue(msgs[-1]["content"].startswith(chat._RIGHT_NOW_LABEL))


class CrossTabPlacementTests(unittest.TestCase):
    """The catch-up is the last FIXED block (kept by the prompt cache); the
    search result is in the changing part (this reply only)."""

    def _build(self, right_now):
        catch = {"role": "system", "content": "CATCH-UP"}
        with mock.patch.object(chat.cross_tab, "catch_up_block", lambda *a, **k: catch), \
                mock.patch.object(store, "load_default_personality_messages", lambda: [_PERSONALITY]), \
                mock.patch.object(ja_voice, "build_voice_rules",
                                  lambda: {"role": "system", "content": "VOICE"}), \
                mock.patch.object(ja_voice, "build_closeness_context",
                                  lambda t: {"role": "system", "content": "CLOSENESS"}), \
                mock.patch.object(stats, "load_stat", lambda _n: 50), \
                mock.patch.object(chat.feelings, "feelings_line", lambda **k: ""), \
                mock.patch.object(chat.concerns, "prompt_line", lambda *a, **k: ""):
            return chat._ordered_prompt(
                {"role": "system", "content": "RULES"}, {"role": "system", "content": "WEB"},
                [{"role": "system", "content": "FOUND"}],
                [{"role": "user", "content": "hi"}], right_now=right_now)

    def test_order_a(self):
        msgs = self._build(False)
        head = msgs[0]["content"]
        self.assertLess(head.index("WEB"), head.index("CATCH-UP"))
        self.assertLess(head.index("CATCH-UP"), head.index("CLOSENESS"))
        self.assertLess(head.index("CATCH-UP"), head.index("FOUND"))

    def test_order_b(self):
        msgs = self._build(True)
        self.assertTrue(msgs[0]["content"].endswith("CATCH-UP"))
        self.assertIn("FOUND", msgs[-1]["content"])
        self.assertNotIn("FOUND", msgs[0]["content"])


class WhichOrderTests(unittest.TestCase):
    """Order B on her built-in model and every cloud provider (prompt-cache
    discount, 2026-11-12); order A on a user's own local / LAN server."""

    def _right_now_for(self, url, builtin=False):
        # chat looks the helpers up on ITS llm module (some test runs put a
        # stand-in there), so the stand-in here goes on chat._llm_module.
        import types
        import llm
        fake = types.SimpleNamespace(
            builtin_llm=types.SimpleNamespace(active=lambda: builtin),
            _configured_server_url=lambda: url,
            _is_local_host=llm._is_local_host)
        with mock.patch.object(chat, "_llm_module", fake):
            return chat._right_now_note()

    def test_both_copies_of_the_label_match(self):
        import llm
        self.assertEqual(chat._RIGHT_NOW_LABEL, llm.RIGHT_NOW_LABEL)

    def test_cloud_providers_use_the_note(self):
        for url in ("https://openrouter.ai/api/v1",
                    "https://generativelanguage.googleapis.com/v1beta/openai",
                    "https://api.groq.com/openai/v1"):
            self.assertTrue(self._right_now_for(url), url)

    def test_own_local_servers_keep_order_a(self):
        for url in ("http://localhost:8080/v1", "http://127.0.0.1:1234/v1",
                    "http://192.168.1.20:8000/v1", "http://amadeus-pc.local:8080/v1"):
            self.assertFalse(self._right_now_for(url), url)

    def test_built_in_model_uses_the_note(self):
        self.assertTrue(self._right_now_for("http://127.0.0.1:8090/v1", builtin=True))


class NoteAsUserMessageTests(unittest.TestCase):
    """llm._right_now_note_as_user: cloud calls send the note as a USER
    message (Gemini folds every system message into its instruction block,
    which made every prompt new); nothing else changes."""

    def _msgs(self):
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
        return [SystemMessage(content="FIXED"),
                HumanMessage(content="hi"), AIMessage(content="やあ"),
                HumanMessage(content="how are you?"),
                SystemMessage(content=chat._RIGHT_NOW_LABEL + "TIMING 10:00")]

    def test_note_becomes_a_user_message(self):
        import llm
        out = llm._right_now_note_as_user(self._msgs())
        self.assertEqual([m.type for m in out], ["system", "human", "ai", "human", "human"])
        self.assertEqual(out[-1].content, chat._RIGHT_NOW_LABEL + "TIMING 10:00")
        self.assertEqual(out[0].content, "FIXED")

    def test_other_system_messages_untouched(self):
        import llm
        from langchain_core.messages import HumanMessage, SystemMessage
        msgs = [SystemMessage(content=chat._RIGHT_NOW_LABEL + "leading - not a note"),
                HumanMessage(content="hi"), SystemMessage(content="another note")]
        out = llm._right_now_note_as_user(msgs)
        self.assertEqual([m.type for m in out], ["system", "human", "system"])

    def _sent_types(self, base_url):
        import llm
        from langchain_openai import ChatOpenAI
        seen = {}

        def fake_generate(self_, messages, stop=None, run_manager=None, **kw):
            seen["types"] = [m.type for m in messages]
            return "ok"

        client = llm._ThoughtFreeChatOpenAI(model="m", api_key="k", base_url=base_url)
        with mock.patch.object(ChatOpenAI, "_generate", fake_generate):
            client._generate(self._msgs())
        return seen["types"]

    def test_cloud_client_sends_the_note_as_user(self):
        self.assertEqual(self._sent_types("https://openrouter.ai/api/v1")[-1], "human")

    def test_local_client_sends_it_unchanged(self):
        self.assertEqual(self._sent_types("http://127.0.0.1:8090/v1")[-1], "system")


class SummaryBatchTests(unittest.TestCase):
    def _batches_for(self, url, builtin=False):
        import llm
        with mock.patch.object(store, "_builtin_active", lambda: builtin), \
                mock.patch.object(llm, "_configured_server_url", lambda: url):
            return store._fold_in_batches()

    def test_cloud_and_built_in_fold_in_batches(self):
        self.assertTrue(self._batches_for("https://openrouter.ai/api/v1"))
        self.assertTrue(self._batches_for("http://127.0.0.1:8090/v1", builtin=True))

    def test_own_local_server_folds_turn_by_turn(self):
        self.assertFalse(self._batches_for("http://localhost:8080/v1"))


if __name__ == "__main__":
    unittest.main()
