"""Slack connection detection via the Web API ``auth.test`` endpoint.

Reuses the same lightweight stdlib (urllib) approach as the Slack channel
adapter — no new dependency and no new notification system. Returns whether the
bot token is valid, the workspace (team) name, and, on failure, the reason
(e.g. ``token_revoked``) used for the disconnection notification.

The HTTP transport is injectable so this is unit-testable without the network.
"""

import json
import urllib.request
from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Tuple

AUTH_TEST_URL = "https://slack.com/api/auth.test"
CONVERSATIONS_INFO_URL = "https://slack.com/api/conversations.info"

# A transport performs one authenticated request and returns (status, body).
Transport = Callable[[str, Mapping[str, str]], Tuple[int, str]]


@dataclass(frozen=True)
class SlackConnection:
    """Result of a Slack connection check."""

    ok: bool
    workspace: Optional[str] = None
    error: Optional[str] = None


def _urllib_transport(url: str, headers: Mapping[str, str]) -> Tuple[int, str]:
    request = urllib.request.Request(url, data=b"", headers=dict(headers), method="POST")
    # nosec B310 - fixed https Slack API endpoint
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, response.read().decode("utf-8")


def check_connection(token: str, transport: Optional[Transport] = None) -> SlackConnection:
    """Return the Slack connection status for *token*.

    Never raises: any transport error is reported as ``ok=False`` with the
    error as the reason.
    """
    transport = transport or _urllib_transport
    headers = {"Authorization": f"Bearer {token}"}

    try:
        status, body = transport(AUTH_TEST_URL, headers)
    except Exception as exc:  # noqa: BLE001 - detection must never raise
        return SlackConnection(ok=False, error=str(exc))

    if status != 200:
        return SlackConnection(ok=False, error=f"HTTP {status}")

    data = json.loads(body or "{}")
    if data.get("ok"):
        return SlackConnection(ok=True, workspace=data.get("team"))
    return SlackConnection(ok=False, error=data.get("error", "unknown_error"))


def _urllib_get_transport(url: str, headers: Mapping[str, str]) -> Tuple[int, str]:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    # nosec B310 - fixed https Slack API endpoint
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, response.read().decode("utf-8")


def resolve_channel_name(
    token: str,
    channel: Optional[str],
    transport: Optional[Transport] = None,
) -> Optional[str]:
    """Return a human-readable channel name (``#name``) for *channel*.

    Best-effort and never raises: if *channel* is already a readable name, or the
    lookup fails / lacks scope, the original value is returned unchanged (so the
    caller falls back to the id). Uses Slack ``conversations.info``.
    """
    if not channel or channel.startswith("#"):
        return channel

    transport = transport or _urllib_get_transport
    url = f"{CONVERSATIONS_INFO_URL}?channel={channel}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        status, body = transport(url, headers)
    except Exception:  # noqa: BLE001 - resolution is best-effort
        return channel

    if status != 200:
        return channel
    data = json.loads(body or "{}")
    name = data.get("channel", {}).get("name") if data.get("ok") else None
    return f"#{name}" if name else channel
