"""Owner's control channel: a private Telegram bot.

- Held posts arrive with [Publish] / [Skip] buttons.
- /pause, /resume, /status work from the owner's chat.
Only updates from TELEGRAM_CHAT_ID are accepted; everything else is ignored.
Bot API docs: https://core.telegram.org/bots/api (getUpdates, sendMessage, sendPhoto,
answerCallbackQuery, editMessageReplyMarkup).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from bayesly import net
from bayesly.publish.base import scrub

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
MAX_TEXT = 4000      # Bot API limit is 4096
MAX_CAPTION = 1000   # Bot API limit is 1024


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, token: str, chat_id: str, *, timeout: float = 20, max_retries: int = 2, session: Any = None) -> None:
        self.token, self.chat_id = token, str(chat_id)
        self.timeout, self.max_retries, self.session = timeout, max_retries, session

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def _call(self, method: str, **kwargs: Any) -> Any:
        if not self.enabled:
            raise TelegramError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not configured")
        try:
            resp = net.request("POST", f"{API}/bot{self.token}/{method}", timeout=self.timeout,
                               max_retries=self.max_retries, session=self.session, **kwargs)
            data = resp.json()
        except (net.ExternalCallError, ValueError) as exc:
            raise TelegramError(scrub(str(exc), self.token)) from None
        if not data.get("ok"):
            raise TelegramError(f"{method}: {str(data)[:300]}")
        return data.get("result")

    # -- sending ---------------------------------------------------------------
    def send(self, text: str, buttons: list[list[tuple[str, str]]] | None = None) -> int | None:
        payload: dict[str, Any] = {"chat_id": self.chat_id, "text": text[:MAX_TEXT], "disable_web_page_preview": True}
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in buttons]}
        result = self._call("sendMessage", json=payload)
        return (result or {}).get("message_id")

    def send_photo(self, path: Path, caption: str = "") -> None:
        with path.open("rb") as fh:
            self._call("sendPhoto", data={"chat_id": self.chat_id, "caption": caption[:MAX_CAPTION]},
                       files={"photo": (path.name, fh, "image/jpeg")})

    def answer(self, callback_id: str, text: str) -> None:
        self._call("answerCallbackQuery", json={"callback_query_id": callback_id, "text": text[:190]})

    def clear_buttons(self, message_id: int) -> None:
        self._call("editMessageReplyMarkup", json={"chat_id": self.chat_id, "message_id": message_id,
                                                   "reply_markup": {"inline_keyboard": []}})

    # -- receiving -------------------------------------------------------------
    def updates(self, offset: int) -> list[dict]:
        return self._call("getUpdates", json={"offset": offset, "timeout": 0,
                                              "allowed_updates": ["message", "callback_query"]}) or []

    def from_owner(self, update: dict) -> bool:
        if "callback_query" in update:
            chat = ((update["callback_query"].get("message") or {}).get("chat") or {})
        else:
            chat = (update.get("message") or {}).get("chat") or {}
        return str(chat.get("id")) == self.chat_id


class OffsetStore:
    """state/telegram.json keeps the getUpdates offset so each update is handled once."""

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / "telegram.json"

    def get(self) -> int:
        if not self.path.exists():
            return 0
        return int(json.loads(self.path.read_text(encoding="utf-8")).get("offset", 0))

    def set(self, offset: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"offset": offset}), encoding="utf-8")
