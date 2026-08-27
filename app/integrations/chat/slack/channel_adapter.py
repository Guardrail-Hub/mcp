"""Slack implementation of the NotificationChannel port.

Delivers a message to a Slack channel via the Web API (``chat.postMessage``) and
edits it in place via ``chat.update``. The HTTP transport is injectable, so the
adapter is unit-testable without network access; the default transport uses the
Python standard library (``urllib``), so no new dependency is introduced.

Slack supports editing a posted message, threads, interactive buttons, and
incremental progress, so it declares all four capabilities. The notification
layer reads those to drive the "one message, keep updating it" strategy.

This module is **transport only**: it authenticates, POSTs, and interprets
Slack's response. Turning notification content into Block Kit belongs to
:class:`~app.integrations.chat.slack.renderer.SlackBlockRenderer`.
"""

import json
import urllib.request
from dataclasses import dataclass
from typing import Callable, Mapping, Optional

from app.core.ports.notification_channel import (
    ChannelCapabilities,
    MessageRef,
    NotificationChannel,
    NotificationContent,
)
from app.integrations.chat.slack.renderer import SlackBlockRenderer


class SlackDeliveryError(RuntimeError):
    """Raised when Slack reports the message was not delivered."""


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """Minimal HTTP response the transport returns to the adapter."""

    status: int
    body: str


# A transport performs one HTTP POST and returns an HttpResponse.
Transport = Callable[[str, Mapping[str, str], bytes], HttpResponse]


def _urllib_transport(url: str, headers: Mapping[str, str], body: bytes) -> HttpResponse:
    """Default transport: POST via the standard library (no third-party deps)."""
    request = urllib.request.Request(
        url, data=body, headers=dict(headers), method="POST"
    )
    # nosec B310 - url is the fixed, https-only Slack API endpoint below
    with urllib.request.urlopen(request, timeout=10) as response:
        return HttpResponse(
            status=response.status, body=response.read().decode("utf-8")
        )


class SlackNotificationChannel(NotificationChannel):
    """Deliver notifications to Slack via ``chat.postMessage`` / ``chat.update``."""

    API_URL = "https://slack.com/api/chat.postMessage"
    UPDATE_URL = "https://slack.com/api/chat.update"

    def __init__(self, token: str, transport: Optional[Transport] = None) -> None:
        """
        Args:
            token: Slack bot token used to authenticate the Web API call.
            transport: HTTP transport that performs the POST. Defaults to a
                standard-library implementation; inject a fake in tests.
        """
        self._token = token
        self._transport = transport or _urllib_transport
        self._renderer = SlackBlockRenderer()

    @property
    def name(self) -> str:
        return "slack"

    @property
    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            supports_message_update=True,
            supports_threads=True,
            supports_buttons=True,
            supports_progress=True,
        )

    def send(
        self,
        destination: str,
        message: str,
        content: Optional[NotificationContent] = None,
    ) -> MessageRef:
        """Post a message to the Slack channel *destination*.

        Args:
            destination: The Slack channel id to post to.
            message: Plain-text body, delivered as Slack's ``text`` fallback.
            content: Structured content the Block Kit layout is built from.

        Returns:
            The Slack message ``ts`` (a :data:`MessageRef` a later
            :meth:`update` targets), or ``None`` if Slack did not return one.

        Raises:
            SlackDeliveryError: If the transport returns a non-200 status or
                Slack reports ``ok = false``.
        """
        payload = self._post(
            self.API_URL,
            {
                "channel": destination,
                "text": message,  # plain-text fallback (notifications, a11y)
                "blocks": self._renderer.render(message, content),
            },
        )
        return payload.get("ts")

    def update(
        self,
        destination: str,
        ref: MessageRef,
        message: str,
        content: Optional[NotificationContent] = None,
    ) -> MessageRef:
        """Edit the message identified by *ref* in the channel *destination*.

        Falls back to posting a new message when *ref* is missing (nothing to
        edit), so a lost handle never drops the update entirely.

        Args:
            destination: The Slack channel id the message lives in.
            ref: The Slack ``ts`` returned by the original :meth:`send`.
            message: The new plain-text body (Slack ``text`` fallback).
            content: Structured content the Block Kit layout is built from.

        Returns:
            The message ``ts`` (unchanged for an edit), for any further update.

        Raises:
            SlackDeliveryError: If the transport returns a non-200 status or
                Slack reports ``ok = false``.
        """
        if not ref:
            return self.send(destination, message, content)
        payload = self._post(
            self.UPDATE_URL,
            {
                "channel": destination,
                "ts": ref,
                "text": message,  # plain-text fallback (notifications, a11y)
                "blocks": self._renderer.render(message, content),
            },
        )
        return payload.get("ts", ref)

    def _post(self, url: str, request_body: dict) -> dict:
        """POST *request_body* to *url* and return Slack's parsed JSON payload.

        Raises:
            SlackDeliveryError: On a non-200 status or a Slack ``ok = false``.
        """
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        body = json.dumps(request_body).encode("utf-8")

        response = self._transport(url, headers, body)
        if response.status != 200:
            raise SlackDeliveryError(f"Slack HTTP {response.status}")

        payload = json.loads(response.body or "{}")
        if not payload.get("ok", False):
            raise SlackDeliveryError(payload.get("error", "unknown_error"))
        return payload
