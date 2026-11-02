"""Offline checks. No Discord connection, API credentials, or paid requests."""

import contextlib
import copy
import io
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
import httpx

import classifier
from bot import ModerationBot, required_setting

ROOT = Path(__file__).resolve().parents[1]


def api_result(probability=0.9):
    return {
        "model": "d1-ultra",
        "answers": {"solicitation": {"type": "noul", "noul": probability}},
    }


class ClassifierTests(unittest.TestCase):
    def test_one_question_request_and_probability(self):
        with patch(
            "httpx.post", return_value=httpx.Response(200, json=api_result())
        ) as post:
            self.assertEqual(
                classifier.score_message("synthetic message", "test-key"), 0.9
            )
        args, kwargs = post.call_args
        self.assertEqual(args, (classifier.ENDPOINT,))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(kwargs["json"]["state"], "synthetic message")
        self.assertEqual(kwargs["json"]["model"], "d1-ultra")
        self.assertEqual(list(kwargs["json"]["questions"]), ["solicitation"])
        self.assertFalse(kwargs["follow_redirects"])
        self.assertEqual(kwargs["timeout"].read, 30)

    def test_invalid_response_never_becomes_a_decision(self):
        invalid = [
            None,
            {},
            [],
            api_result(True),
            api_result("0.9"),
            api_result(-0.1),
            api_result(1.1),
            api_result(float("nan")),
            api_result(float("inf")),
        ]
        wrong_model = api_result()
        wrong_model["model"] = "another-model"
        wrong_type = api_result()
        wrong_type["answers"]["solicitation"]["type"] = "choice"
        invalid.extend([wrong_model, wrong_type])
        for result in invalid:
            with (
                self.subTest(result=result),
                self.assertRaises(classifier.LiquidAPIError),
            ):
                classifier.parse_probability(result)
        self.assertEqual(classifier.parse_probability(api_result(0)), 0)
        self.assertEqual(classifier.parse_probability(api_result(1)), 1)

    def test_api_failures_do_not_leak_response_or_retry(self):
        for status in [401, 402, 403, 429, 500, 302]:
            with self.subTest(status=status):
                with patch(
                    "httpx.post",
                    return_value=httpx.Response(status, text="private text"),
                ) as post:
                    with self.assertRaises(classifier.LiquidAPIError) as raised:
                        classifier.score_message("synthetic", "test-key")
                    self.assertNotIn("private text", str(raised.exception))
                    self.assertIn(str(status), str(raised.exception))
                    post.assert_called_once()

    def test_connection_failure_and_non_json(self):
        with patch("httpx.post", side_effect=httpx.ConnectError("private text")):
            with self.assertRaises(classifier.LiquidAPIError) as raised:
                classifier.score_message("synthetic", "test-key")
            self.assertNotIn("private text", str(raised.exception))
        with patch("httpx.post", return_value=httpx.Response(200, text="not JSON")):
            with self.assertRaises(classifier.LiquidAPIError):
                classifier.score_message("synthetic", "test-key")

    def test_missing_credentials_make_no_request(self):
        with patch("httpx.post") as post, self.assertRaises(ValueError):
            classifier.score_message("synthetic", " ")
        post.assert_not_called()
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            required_setting("LIQUID_API_KEY")


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = ModerationBot(10, 20, "test-key")
        self.bot.review_channel = SimpleNamespace(send=AsyncMock())
        self.message = SimpleNamespace(
            id=30,
            guild=SimpleNamespace(id=1),
            channel=SimpleNamespace(id=10),
            author=SimpleNamespace(bot=False),
            webhook_id=None,
            content="Synthetic test message",
            jump_url="https://discord.com/channels/1/10/30",
            delete=AsyncMock(),
        )

    async def asyncTearDown(self):
        await self.bot.close()

    async def test_threshold_boundary_and_no_deletion(self):
        for score, reports in [(0.799, 0), (0.8, 1), (1, 1)]:
            with (
                self.subTest(score=score),
                patch("bot.score_message", return_value=score),
            ):
                self.bot.review_channel.send.reset_mock()
                await self.bot.on_message(self.message)
                self.assertEqual(self.bot.review_channel.send.await_count, reports)
                self.message.delete.assert_not_awaited()

    async def test_out_of_scope_events_make_no_api_call(self):
        overrides = [
            {"guild": None},
            {"channel": SimpleNamespace(id=20)},
            {"channel": SimpleNamespace(id=99)},
            {"author": SimpleNamespace(bot=True)},
            {"webhook_id": 40},
            {"content": "  "},
        ]
        for override in overrides:
            message = copy.copy(self.message)
            message.__dict__.update(override)
            with self.subTest(override=override), patch("bot.score_message") as score:
                await self.bot.on_message(message)
                score.assert_not_called()
        self.bot.review_channel.send.assert_not_awaited()

    async def test_api_error_sends_no_report(self):
        with patch(
            "bot.score_message",
            side_effect=classifier.LiquidAPIError("API unavailable"),
        ):
            with self.assertLogs("bot", level="WARNING"):
                await self.bot.on_message(self.message)
        self.bot.review_channel.send.assert_not_awaited()
        self.message.delete.assert_not_awaited()
        self.assertFalse(self.bot.busy.locked())

    async def test_report_failure_releases_lock(self):
        self.bot.review_channel.send.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions"
        )
        with (
            patch("bot.score_message", return_value=0.95),
            self.assertLogs("bot", level="WARNING"),
        ):
            await self.bot.on_message(self.message)
        self.message.delete.assert_not_awaited()
        self.assertFalse(self.bot.busy.locked())

    async def test_report_is_bounded_and_mentions_are_disabled(self):
        self.message.content = "@everyone **sales** " * 300
        with patch("bot.score_message", return_value=0.95):
            await self.bot.on_message(self.message)
        kwargs = self.bot.review_channel.send.call_args.kwargs
        self.assertLessEqual(len(kwargs["embed"].description), 4096)
        self.assertEqual(kwargs["allowed_mentions"].to_dict(), {"parse": []})
        self.assertIn(self.message.jump_url, kwargs["embed"].fields[1].value)

    async def test_busy_bot_and_not_ready_bot_make_no_request(self):
        async with self.bot.busy:
            with (
                patch("bot.score_message") as score,
                self.assertLogs("bot", level="WARNING"),
            ):
                await self.bot.on_message(self.message)
                score.assert_not_called()
        self.bot.review_channel = None
        with patch("bot.score_message") as score:
            await self.bot.on_message(self.message)
            score.assert_not_called()

    async def test_startup_requires_same_server_and_channel_permissions(self):
        def channel(guild_id):
            value = Mock(spec=discord.TextChannel)
            value.guild = SimpleNamespace(id=guild_id, me=object())
            value.permissions_for.return_value = SimpleNamespace(
                view_channel=True, send_messages=True, embed_links=True
            )
            return value

        source, review = channel(1), channel(1)
        with patch.object(self.bot, "get_channel", side_effect=[source, review]):
            await self.bot.on_ready()
        self.assertIs(self.bot.review_channel, review)
        review.guild.id = 2
        with patch.object(self.bot, "get_channel", side_effect=[source, review]):
            with patch.object(self.bot, "close", new_callable=AsyncMock) as close:
                with self.assertLogs("bot", level="ERROR"):
                    await self.bot.on_ready()
                close.assert_awaited_once()
        self.assertIsNone(self.bot.review_channel)
        review.guild.id = 1
        review.permissions_for.return_value.embed_links = False
        with patch.object(self.bot, "get_channel", side_effect=[source, review]):
            with patch.object(self.bot, "close", new_callable=AsyncMock) as close:
                with self.assertLogs("bot", level="ERROR"):
                    await self.bot.on_ready()
                close.assert_awaited_once()
        self.assertIsNone(self.bot.review_channel)


class NotebookTests(unittest.TestCase):
    def test_notebook_runs_in_order_with_mock_api_and_matches_bot_policy(self):
        notebook = json.loads(
            (ROOT / "notebooks/d1_ultra_solicitation.ipynb").read_text()
        )
        namespace = {}
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("getpass.getpass", return_value="test-key"),
        ):
            with patch(
                "httpx.post", return_value=httpx.Response(200, json=api_result())
            ) as post:
                with contextlib.redirect_stdout(io.StringIO()):
                    for index, cell in enumerate(notebook["cells"]):
                        if cell["cell_type"] != "code":
                            continue
                        self.assertEqual(cell["outputs"], [])
                        self.assertIsNone(cell["execution_count"])
                        source = "".join(cell["source"])
                        if source.startswith("%pip"):
                            continue  # Dependencies are already installed by requirements.txt.
                        exec(
                            compile(source, f"notebook-cell-{index}", "exec"), namespace
                        )
                self.assertEqual(post.call_count, 7)
                for call in post.call_args_list:
                    self.assertEqual(
                        call.kwargs["json"]["questions"],
                        classifier.build_payload("")["questions"],
                    )
        self.assertEqual(namespace["INSTRUCTIONS"], classifier.INSTRUCTIONS)
        self.assertEqual(namespace["REVIEW_THRESHOLD"], classifier.REVIEW_THRESHOLD)
        self.assertEqual(namespace["decision"](0.8), "review")
        self.assertEqual(namespace["decision"](0.799), "no flag")
        self.assertEqual(len(namespace["results"]), 6)


if __name__ == "__main__":
    unittest.main()
