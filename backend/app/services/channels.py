"""Sending to Telegram (Bot API over httpx) and Web Push (pywebpush).

Payloads are short texts and an app link; never log them. A channel that tells us the
user is gone (push endpoint 404/410, Telegram bot blocked) comes back as dead.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import requests
from pywebpush import WebPushException, webpush

from app.core.config import Settings
from app.models.enums import ChannelType

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
PUSH_TTL_S = 6 * 3600  # an undelivered reminder is useless after a few hours


@dataclass(frozen=True)
class Outgoing:
    title: str
    body: str
    url: str  # where a tap opens
    tag: str  # the reminder type: a newer one replaces an older one on the device


@dataclass(frozen=True)
class SendResult:
    ok: bool
    dead: bool = False  # the device or chat is gone: remove the channel


class Dispatcher(Protocol):
    def send(
        self, channel: ChannelType, address: dict[str, Any], message: Outgoing
    ) -> SendResult: ...


class TelegramError(Exception):
    def __init__(self, status: int, description: str = "") -> None:
        super().__init__(f"telegram error {status}")
        self.status = status
        self.description = description


class Telegram:
    def __init__(self, token: str, client: httpx.Client | None = None) -> None:
        self._token = token
        self._client = client or httpx.Client(timeout=15)

    def call(self, method: str, **params: Any) -> Any:
        res = self._client.post(
            f"{TELEGRAM_API}/bot{self._token}/{method}", json=params
        )
        body = res.json() if res.content else {}
        if res.status_code != 200 or not body.get("ok"):
            raise TelegramError(res.status_code, str(body.get("description", ""))[:200])
        return body["result"]

    def send_text(self, chat_id: int, text: str) -> None:
        self.call(
            "sendMessage",
            chat_id=chat_id,
            text=text,
            link_preview_options={"is_disabled": True},
        )

    def send(self, chat_id: int, message: Outgoing) -> SendResult:
        try:
            self.send_text(chat_id, f"{message.body}\n{message.url}")
        except TelegramError as exc:
            # 403: the user blocked the bot or deleted the chat.
            logger.warning("telegram_send_failed status=%s", exc.status)
            return SendResult(False, dead=exc.status == 403)
        except httpx.HTTPError as exc:
            logger.warning("telegram_send_failed error=%s", type(exc).__name__)
            return SendResult(False)
        return SendResult(True)

    def username(self) -> str:
        return str(self.call("getMe")["username"])

    def updates(self, offset: int | None, timeout_s: int = 0) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": timeout_s, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        return list(self.call("getUpdates", **params))

    def set_webhook(self, url: str, secret: str) -> None:
        self.call(
            "setWebhook", url=url, secret_token=secret, allowed_updates=["message"]
        )

    def delete_webhook(self) -> None:
        self.call("deleteWebhook")


def send_webpush(
    subscription: dict[str, Any], message: Outgoing, settings: Settings
) -> SendResult:
    payload = {
        "title": message.title,
        "body": message.body,
        "url": message.url,
        "tag": message.tag,
    }
    try:
        webpush(
            subscription_info=subscription,
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": settings.vapid_subject},
            ttl=PUSH_TTL_S,
            timeout=15,
        )
    except WebPushException as exc:
        status = exc.response.status_code if exc.response is not None else 0
        logger.warning("webpush_send_failed status=%s", status)
        return SendResult(False, dead=status in (404, 410))
    except (requests.RequestException, ValueError) as exc:  # network: next slot
        logger.warning("webpush_send_failed error=%s", type(exc).__name__)
        return SendResult(False)
    return SendResult(True)


class RealDispatcher:
    def __init__(self, settings: Settings, telegram: Telegram | None = None) -> None:
        self._settings = settings
        self._telegram = telegram or (
            Telegram(settings.telegram_bot_token)
            if settings.telegram_bot_token
            else None
        )

    def send(
        self, channel: ChannelType, address: dict[str, Any], message: Outgoing
    ) -> SendResult:
        if channel is ChannelType.TELEGRAM:
            if self._telegram is None:
                return SendResult(False)
            return self._telegram.send(int(address["chat_id"]), message)
        if not self._settings.vapid_private_key:
            return SendResult(False)
        return send_webpush(address, message, self._settings)
