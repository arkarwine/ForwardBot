from __future__ import annotations

import asyncio
import html
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from pyrogram import Client
from pyrogram.errors import ChannelPrivate, ChatAdminRequired, FloodWait, MessageIdInvalid, PeerIdInvalid, RPCError
from pyrogram.types import Message

from .links import MessageLink
from .sessions import SessionManager


class CopyError(Exception):
    def __init__(self, message: str, *, needs_private_help: bool = False) -> None:
        super().__init__(message)
        self.needs_private_help = needs_private_help


class BotCopyReturnedEmpty(Exception):
    pass


class PublicTelegramTextParser(HTMLParser):
    def __init__(self, target_post: str) -> None:
        super().__init__(convert_charrefs=False)
        self.target_post = target_post
        self._message_depth = 0
        self._text_depth = 0
        self._parts: list[str] = []

    @property
    def text(self) -> str | None:
        text = html.unescape("".join(self._parts))
        lines = [line.strip() for line in text.splitlines()]
        text = "\n".join(lines).strip()
        return text or None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        class_names = attrs_dict.get("class", "")

        if tag == "div" and "tgme_widget_message_wrap" in class_names:
            if attrs_dict.get("data-post") == self.target_post:
                self._message_depth = 1
            elif self._message_depth:
                self._message_depth += 1
            return

        if self._message_depth:
            self._message_depth += 1
            if tag == "div" and "tgme_widget_message_text" in class_names:
                self._text_depth = 1
            elif self._text_depth:
                self._text_depth += 1

        if self._text_depth and tag == "br":
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._text_depth:
            self._text_depth -= 1
        if self._message_depth:
            self._message_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._text_depth:
            if not data.strip():
                return
            self._parts.append(data.strip() if "\n" in data else data)

    def handle_entityref(self, name: str) -> None:
        if self._text_depth:
            self._parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._text_depth:
            self._parts.append(f"&#{name};")


def _message_protected(message: Message) -> bool:
    return bool(
        getattr(message, "has_protected_content", False)
        or getattr(getattr(message, "chat", None), "has_protected_content", False)
    )


def _message_empty(message: Message) -> bool:
    return bool(getattr(message, "empty", False))


async def copy_message(
    bot: Client,
    session_manager: SessionManager,
    link: MessageLink,
    target_chat: str | int,
    default_session_name: str,
    download_dir: Path,
) -> str:
    try:
        copied = await bot.copy_message(
            chat_id=target_chat,
            from_chat_id=link.chat_ref,
            message_id=link.message_id,
        )
        if copied is None or _message_empty(copied):
            raise BotCopyReturnedEmpty("Telegram returned an empty copy result.")
        return "Copied with the bot."
    except FloodWait as exc:
        raise CopyError(f"Telegram asked to wait {exc.value} seconds. Try again shortly.") from exc
    except (
        BotCopyReturnedEmpty,
        ChannelPrivate,
        PeerIdInvalid,
        MessageIdInvalid,
        ChatAdminRequired,
        RPCError,
    ) as bot_exc:
        if isinstance(bot_exc, BotCopyReturnedEmpty) and await clone_public_web_text(bot, link, target_chat):
            return "Cloned from Telegram's public web preview because the bot copy returned empty."

        user_client = await _get_default_user_client(session_manager, default_session_name, bot_exc)
        try:
            source_message = await user_client.get_messages(link.chat_ref, link.message_id)
        except (ChannelPrivate, PeerIdInvalid) as exc:
            if link.is_private_internal:
                raise CopyError(
                    "That private link is not accessible to the bot or default user session. "
                    "Use /join INVITE_LINK or /login an account that is already in the source.",
                    needs_private_help=True,
                ) from exc
            raise CopyError(
                "The default user session cannot access that public source. "
                "Check that the channel exists or refresh the user session with /login.",
            ) from exc
        except MessageIdInvalid as exc:
            raise CopyError("I could access the chat, but that message id was not found.") from exc

        if not source_message:
            raise CopyError("I could access the chat, but Telegram returned no message for that id.")
        if _message_empty(source_message):
            if await clone_public_web_text(bot, link, target_chat):
                return "Cloned from Telegram's public web preview because MTProto returned empty."
            raise CopyError(
                "Telegram returned this as an empty message. It may be deleted, inaccessible to the user session, "
                "or not a normal content message."
            )

        if getattr(source_message, "media_group_id", None):
            group = await user_client.get_media_group(link.chat_ref, link.message_id)
            for item in group:
                await reupload_message(bot, item, target_chat, download_dir)
            return "Downloaded and re-uploaded the album with the user session."

        if _message_protected(source_message):
            await reupload_message(bot, source_message, target_chat, download_dir)
            return "Downloaded with the user session and re-uploaded because the source is protected."

        await reupload_message(bot, source_message, target_chat, download_dir)
        return "Downloaded with the user session and re-uploaded with the bot."


async def clone_public_web_text(bot: Client, link: MessageLink, target_chat: str | int) -> bool:
    text = await fetch_public_web_text(link)
    if not text:
        return False
    await bot.send_message(target_chat, text, disable_web_page_preview=True)
    return True


async def fetch_public_web_text(link: MessageLink) -> str | None:
    if link.is_private_internal or not isinstance(link.chat_ref, str):
        return None

    return await asyncio.to_thread(_fetch_public_web_text_sync, link.chat_ref, link.message_id)


def _fetch_public_web_text_sync(username: str, message_id: int) -> str | None:
    url = f"https://t.me/s/{username}/{message_id}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 ForwardBot/0.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read().decode("utf-8", errors="replace")
    except OSError:
        return None

    parser = PublicTelegramTextParser(f"{username}/{message_id}")
    parser.feed(body)
    return parser.text


async def _get_default_user_client(
    session_manager: SessionManager,
    default_session_name: str,
    original_error: Exception,
) -> Client:
    try:
        return await session_manager.ensure_started(default_session_name)
    except Exception as exc:
        raise CopyError(
            "The bot could not access the source, and the default user session is not logged in. "
            f"Run /login {default_session_name} first.",
        ) from exc


async def reupload_message(
    bot: Client,
    message: Message,
    target_chat: str | int,
    download_dir: Path,
) -> None:
    download_dir.mkdir(parents=True, exist_ok=True)

    if _message_empty(message):
        raise CopyError("Telegram returned an empty message, so there is no content to send.")

    if message.text:
        await bot.send_message(target_chat, message.text, disable_web_page_preview=True)
        return

    caption = message.caption if getattr(message, "caption", None) else None
    kwargs: dict[str, Any] = {"caption": caption} if caption else {}

    if message.photo:
        path = await message.download(file_name=str(download_dir / "photo_"))
        await bot.send_photo(target_chat, path, **kwargs)
        Path(path).unlink(missing_ok=True)
    elif message.video:
        path = await message.download(file_name=str(download_dir / "video_"))
        await bot.send_video(target_chat, path, **kwargs)
        Path(path).unlink(missing_ok=True)
    elif message.animation:
        path = await message.download(file_name=str(download_dir / "animation_"))
        await bot.send_animation(target_chat, path, **kwargs)
        Path(path).unlink(missing_ok=True)
    elif message.audio:
        path = await message.download(file_name=str(download_dir / "audio_"))
        await bot.send_audio(target_chat, path, **kwargs)
        Path(path).unlink(missing_ok=True)
    elif message.voice:
        path = await message.download(file_name=str(download_dir / "voice_"))
        await bot.send_voice(target_chat, path, **kwargs)
        Path(path).unlink(missing_ok=True)
    elif message.video_note:
        path = await message.download(file_name=str(download_dir / "video_note_"))
        await bot.send_video_note(target_chat, path)
        if caption:
            await bot.send_message(target_chat, caption)
        Path(path).unlink(missing_ok=True)
    elif message.sticker:
        path = await message.download(file_name=str(download_dir / "sticker_"))
        await bot.send_sticker(target_chat, path)
        Path(path).unlink(missing_ok=True)
    elif message.document:
        path = await message.download(file_name=str(download_dir / "document_"))
        await bot.send_document(target_chat, path, **kwargs)
        Path(path).unlink(missing_ok=True)
    elif message.location:
        await bot.send_location(
            target_chat,
            latitude=message.location.latitude,
            longitude=message.location.longitude,
        )
    elif message.venue:
        await bot.send_venue(
            target_chat,
            latitude=message.venue.location.latitude,
            longitude=message.venue.location.longitude,
            title=message.venue.title,
            address=message.venue.address,
        )
    elif message.contact:
        await bot.send_contact(
            target_chat,
            phone_number=message.contact.phone_number,
            first_name=message.contact.first_name,
            last_name=message.contact.last_name or "",
        )
    elif message.poll:
        poll = message.poll
        await bot.send_poll(
            target_chat,
            question=poll.question,
            options=[option.text for option in poll.options],
            is_anonymous=poll.is_anonymous,
            type=poll.type,
            allows_multiple_answers=poll.allows_multiple_answers,
        )
    else:
        raise CopyError("This message type is not supported for re-upload yet.")
