"""Alert delivery to phone via ntfy or Pushover (Section 2.3).

Alert payloads include the deep link and (where available) the price-context
computation. If no notifier is configured, alerts are still persisted to
SQLite and logged — delivery degrades, the record does not.
"""

from __future__ import annotations

import logging

from ..config import Config
from ..models import Alert

log = logging.getLogger("product_intel.notifier")


class Notifier:
    def __init__(self, config: Config, timeout: float = 10.0):
        self._c = config
        self._timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self._c.ntfy_topic) or bool(self._c.pushover_token and self._c.pushover_user)

    def send(self, alert: Alert) -> bool:
        """Best-effort delivery; returns True if at least one channel accepted."""
        delivered = False
        if self._c.ntfy_topic:
            delivered = self._send_ntfy(alert) or delivered
        if self._c.pushover_token and self._c.pushover_user:
            delivered = self._send_pushover(alert) or delivered
        if not self.configured:
            log.warning("No notifier configured; alert persisted only: %s", alert.message)
        return delivered

    def _send_ntfy(self, alert: Alert) -> bool:
        import httpx

        url = f"{self._c.ntfy_base_url.rstrip('/')}/{self._c.ntfy_topic}"
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    url,
                    data=alert.message.encode("utf-8"),
                    headers={
                        "Title": f"[{alert.layer.value}] price alert",
                        "Click": alert.deep_link,
                        "Tags": "moneybag",
                    },
                )
                resp.raise_for_status()
            return True
        except Exception as e:  # noqa: BLE001 — delivery is best-effort
            log.error("ntfy delivery failed: %s", e)
            return False

    def _send_pushover(self, alert: Alert) -> bool:
        import httpx

        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    "https://api.pushover.net/1/messages.json",
                    data={
                        "token": self._c.pushover_token,
                        "user": self._c.pushover_user,
                        "title": f"[{alert.layer.value}] price alert",
                        "message": alert.message,
                        "url": alert.deep_link,
                        "url_title": "Open product",
                    },
                )
                resp.raise_for_status()
            return True
        except Exception as e:  # noqa: BLE001
            log.error("pushover delivery failed: %s", e)
            return False
