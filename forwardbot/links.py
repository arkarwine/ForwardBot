from __future__ import annotations

import re
from dataclasses import dataclass


PUBLIC_RE = re.compile(
    r"^(?:https?://)?t\.me/(?P<username>[A-Za-z0-9_]{5,32})/(?P<msg_id>\d+)(?:\?.*)?$"
)
PRIVATE_RE = re.compile(
    r"^(?:https?://)?t\.me/c/(?P<internal_id>\d+)/(?P<msg_id>\d+)(?:\?.*)?$"
)


@dataclass(frozen=True)
class MessageLink:
    raw: str
    chat_ref: str | int
    message_id: int
    is_private_internal: bool

    @property
    def display_source(self) -> str:
        return str(self.chat_ref)


def parse_message_link(link: str) -> MessageLink:
    link = link.strip()
    public = PUBLIC_RE.match(link)
    if public:
        return MessageLink(
            raw=link,
            chat_ref=public.group("username"),
            message_id=int(public.group("msg_id")),
            is_private_internal=False,
        )

    private = PRIVATE_RE.match(link)
    if private:
        internal_id = private.group("internal_id")
        return MessageLink(
            raw=link,
            chat_ref=int(f"-100{internal_id}"),
            message_id=int(private.group("msg_id")),
            is_private_internal=True,
        )

    raise ValueError(
        "Send a Telegram message link like https://t.me/channel/123 or https://t.me/c/123456789/123."
    )
