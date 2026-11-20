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
    @staticmethod
    def _markup(buttons: list[list[tuple[str, str]]] | None) -> dict | None:
        """Rows of (label, value); a value starting with http(s) becomes a link button."""
        if not buttons:
            return None
        return {"inline_keyboard": [[
            {"text": t, "url": v} if v.startswith(("http://", "https://")) else {"text": t, "callback_data": v}
            for t, v in row] for row in buttons]}

    def send(self, text: str, buttons: list[list[tuple[str, str]]] | None = None, html: bool = False) -> int | None:
        payload: dict[str, Any] = {"chat_id": self.chat_id, "text": text[:MAX_TEXT],
                                   "link_preview_options": {"is_disabled": True}}
        if html:
            payload["parse_mode"] = "HTML"
        markup = self._markup(buttons)
        if markup:
            payload["reply_markup"] = markup
        result = self._call("sendMessage", json=payload)
        return (result or {}).get("message_id")

    def send_photo(self, path: Path, caption: str = "", buttons: list[list[tuple[str, str]]] | None = None,
                   html: bool = False) -> int | None:
        data: dict[str, Any] = {"chat_id": self.chat_id, "caption": caption[:MAX_CAPTION]}
        if html:
            data["parse_mode"] = "HTML"
        markup = self._markup(buttons)
        if markup:
            data["reply_markup"] = json.dumps(markup)
        with path.open("rb") as fh:
            result = self._call("sendPhoto", data=data, files={"photo": (path.name, fh, "image/jpeg")})
        return (result or {}).get("message_id")

    def answer(self, callback_id: str, text: str) -> None:
        self._call("answerCallbackQuery", json={"callback_query_id": callback_id, "text": text[:190]})

    def clear_buttons(self, message_id: int) -> None:
        self._call("editMessageReplyMarkup", json={"chat_id": self.chat_id, "message_id": message_id,
                                                   "reply_markup": {"inline_keyboard": []}})

    def edit_caption(self, message_id: int, caption: str, buttons: list[list[tuple[str, str]]] | None = None) -> None:
        """Replace a held post's caption after the owner decides (falls back to text edit for text messages)."""
        payload: dict[str, Any] = {"chat_id": self.chat_id, "message_id": message_id, "parse_mode": "HTML",
                                   "reply_markup": self._markup(buttons) or {"inline_keyboard": []}}
        try:
            self._call("editMessageCaption", json={**payload, "caption": caption[:MAX_CAPTION]})
        except TelegramError:
            self._call("editMessageText", json={**payload, "text": caption[:MAX_TEXT]})

    def set_commands(self, commands: list[tuple[str, str]], description: str = "") -> None:
        self._call("setMyCommands", json={"commands": [{"command": c, "description": d} for c, d in commands]})
        if description:
            self._call("setMyShortDescription", json={"short_description": description[:120]})

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
