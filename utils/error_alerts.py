"""
Post ERROR log records to a Discord channel through a webhook, so production failures get noticed.
Records are handed to a background thread, so a slow webhook never blocks the bot's event loop.
"""
import json
import logging
import logging.handlers
import queue
import time
import urllib.request

# Discord rejects message content longer than this.
MAX_CONTENT_LENGTH = 2000


class DiscordWebhookHandler(logging.Handler):
    """Logging handler that posts each record to a Discord webhook, at most max_per_minute times a minute."""

    def __init__(self, url: str, max_per_minute: int = 10):
        super().__init__(logging.ERROR)
        self.url = url
        self.max_per_minute = max_per_minute
        self._window_start = None
        self._sent_in_window = 0
        self._suppressed = 0

    def emit(self, record: logging.LogRecord) -> None:
        now = time.monotonic()
        if self._window_start is None or now - self._window_start >= 60:
            self._window_start = now
            self._sent_in_window = 0
        if self._sent_in_window >= self.max_per_minute:
            self._suppressed += 1
            return
        self._sent_in_window += 1

        try:
            self._post(self.build_content(record))
            self._suppressed = 0
        except Exception:
            # Never log from here: this handler would receive its own error and loop.
            self.handleError(record)

    def build_content(self, record: logging.LogRecord) -> str:
        header = f"🚨 **{record.levelname}** in `{record.name}`"
        if self._suppressed:
            header += f" ({self._suppressed} earlier alerts suppressed)"
        # Keep the end of long tracebacks: that is where the exception is.
        body = self.format(record).replace("```", "`​``")
        room = MAX_CONTENT_LENGTH - len(header) - len("\n```\n\n```")
        if len(body) > room:
            body = "…" + body[-(room - 1):]
        return f"{header}\n```\n{body}\n```"

    def _post(self, content: str) -> None:
        payload = json.dumps({"content": content, "allowed_mentions": {"parse": []}}).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=payload,
            # Discord's edge rejects the default urllib user agent.
            headers={"Content-Type": "application/json", "User-Agent": "NBLBot error alerts"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10):
            pass


def install_error_alerts(url: str, fmt: str) -> logging.handlers.QueueListener:
    """Send ERROR logs from every logger to the webhook. Call stop() on the result at shutdown to flush."""
    records = queue.SimpleQueue()
    webhook_handler = DiscordWebhookHandler(url)
    webhook_handler.setFormatter(logging.Formatter(fmt))

    queue_handler = logging.handlers.QueueHandler(records)
    queue_handler.setLevel(logging.ERROR)
    logging.getLogger().addHandler(queue_handler)

    listener = logging.handlers.QueueListener(records, webhook_handler, respect_handler_level=True)
    listener.start()
    return listener
