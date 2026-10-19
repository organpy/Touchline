"""Tests for posting ERROR logs to a Discord webhook."""

import json
import logging
from unittest.mock import MagicMock, patch

from utils.error_alerts import MAX_CONTENT_LENGTH, DiscordWebhookHandler, install_error_alerts

URL = "https://discord.com/api/webhooks/1/token"


def _record(message, name="cogs.trades", exc_info=None):
    return logging.LogRecord(name, logging.ERROR, __file__, 1, message, None, exc_info)


def _posted(urlopen):
    return [json.loads(call.args[0].data) for call in urlopen.call_args_list]


def test_error_is_posted_without_pings():
    handler = DiscordWebhookHandler(URL)
    with patch("utils.error_alerts.urllib.request.urlopen", MagicMock()) as urlopen:
        handler.emit(_record("Trade failed for @everyone"))

    [payload] = _posted(urlopen)
    assert "`cogs.trades`" in payload["content"]
    assert "Trade failed for @everyone" in payload["content"]
    assert payload["allowed_mentions"] == {"parse": []}


def test_long_traceback_keeps_the_exception_line():
    handler = DiscordWebhookHandler(URL)
    try:
        raise RuntimeError("the real cause")
    except RuntimeError:
        import sys
        record = _record("x" * 5000, exc_info=sys.exc_info())

    content = handler.build_content(record)

    assert len(content) <= MAX_CONTENT_LENGTH
    assert "RuntimeError: the real cause" in content


def test_alerts_are_rate_limited_and_report_suppressed_count():
    handler = DiscordWebhookHandler(URL, max_per_minute=2)
    with patch("utils.error_alerts.urllib.request.urlopen", MagicMock()) as urlopen, \
            patch("utils.error_alerts.time.monotonic", side_effect=[0, 1, 2, 61]):
        for _ in range(4):
            handler.emit(_record("boom"))

    posted = _posted(urlopen)
    assert len(posted) == 3
    assert "1 earlier alerts suppressed" in posted[-1]["content"]


def test_webhook_failure_does_not_raise():
    handler = DiscordWebhookHandler(URL)
    with patch("utils.error_alerts.urllib.request.urlopen", side_effect=OSError("down")), \
            patch.object(logging, "raiseExceptions", False):
        handler.emit(_record("boom"))


def test_installed_alerts_send_logged_errors():
    root = logging.getLogger()
    handlers_before = list(root.handlers)
    with patch("utils.error_alerts.urllib.request.urlopen", MagicMock()) as urlopen:
        listener = install_error_alerts(URL, "%(levelname)s %(message)s")
        try:
            logging.getLogger("cogs.score").warning("not an alert")
            logging.getLogger("cogs.score").error("score save failed")
        finally:
            listener.stop()
            for handler in root.handlers[:]:
                if handler not in handlers_before:
                    root.removeHandler(handler)

    [payload] = _posted(urlopen)
    assert "ERROR score save failed" in payload["content"]
