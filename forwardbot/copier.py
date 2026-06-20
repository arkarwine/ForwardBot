from __future__ import annotations

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


def _message_protected(message: Message) -> bool:
    return bool(
        getattr(message, "has_protected_content", False)
        or getattr(getattr(message, "chat", None), "has_protected_content", False)
    )


async def copy_message(
    bot: Client,
    session_manager: SessionManager,
    link: MessageLink,
    target_chat: str | int,
    default_session_name: str,
    download_dir: Path,
) -> str:
    try:
        await bot.copy_message(
            chat_id=target_chat,
            from_chat_id=link.chat_ref,
            message_id=link.message_id,
        )
        return "Copied with the bot."
    except FloodWait as exc:
        raise CopyError(f"Telegram asked to wait {exc.value} seconds. Try again shortly.") from exc
    except (ChannelPrivate, PeerIdInvalid, MessageIdInvalid, ChatAdminRequired, RPCError) as bot_exc:
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
