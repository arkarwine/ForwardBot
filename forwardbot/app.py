from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from pyrogram import Client, filters
from pyrogram.errors import (
    BadRequest,
    PasswordHashInvalid,
    PhoneCodeExpired,
    PhoneCodeInvalid,
    SessionPasswordNeeded,
)
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .config import Settings
from .copier import CopyError, clone_with_client, copy_message
from .db import Database
from .links import MessageLink, parse_message_link
from .sessions import SessionManager, SessionUnavailable

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


@dataclass
class PrivateCopyFlow:
    link: MessageLink
    requester_id: int
    status_chat_id: int
    status_message_id: int | None
    target_chat: int
    step: str = "choose"
    client: Client | None = None
    phone: str | None = None
    phone_code_hash: str | None = None
    login_code: str = ""
    session_string: str | None = None


def run() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    settings = Settings.load()
    settings.download_dir.mkdir(parents=True, exist_ok=True)
    settings.session_dir.mkdir(parents=True, exist_ok=True)

    db = Database(settings.db_path)
    db.init()
    sessions = SessionManager(settings, db, loop)

    bot = Client(
        "forwardbot",
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        bot_token=settings.bot_token,
        workdir=str(settings.session_dir),
        loop=loop,
    )
    flows: dict[int, PrivateCopyFlow] = {}

    @bot.on_message(filters.command(["start", "help"]))
    async def start_handler(_: Client, message: Message) -> None:
        await message.reply_text(
            "Send /copy MESSAGE_LINK.\n\n"
            "Public links are cloned with the configured default user session and sent to your private chat.\n"
            "Private links will ask how you want to give access: invite link or a temporary member login."
        )

    @bot.on_message(filters.command("cancel"))
    async def cancel_handler(_: Client, message: Message) -> None:
        flow = flows.pop(message.from_user.id, None) if message.from_user else None
        if flow and flow.client and flow.client.is_connected:
            await flow.client.disconnect()
        await message.reply_text("Cancelled the current copy flow.")

    @bot.on_message(filters.command("copy"))
    async def copy_handler(_: Client, message: Message) -> None:
        if not message.from_user:
            await message.reply_text(
                "I need a visible user sender so I know where to DM the result."
            )
            return

        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.reply_text("Usage: /copy MESSAGE_LINK")
            return

        try:
            link = parse_message_link(parts[1])
        except ValueError as exc:
            await message.reply_text(str(exc))
            return

        target_chat = message.from_user.id
        if link.is_private_internal:
            await start_private_copy_flow(message, link, target_chat)
            return

        job_id = db.create_job(message.from_user.id, link.raw, str(target_chat))
        status = await message.reply_text("Cloning public post...")
        try:
            detail = await copy_message(
                bot,
                sessions,
                link,
                target_chat,
                settings.default_user_session,
                settings.download_dir,
            )
            db.update_job(job_id, "sent", detail)
            await status.edit_text(f"Done. Sent to your private chat. {detail}")
        except CopyError as exc:
            db.update_job(job_id, "failed", str(exc))
            await status.edit_text(
                f"I could not clone that public post.\n\n{exc}\n\n"
                "Check that DEFAULT_USER_SESSION_STRING is configured and that the linked post is visible "
                "to that account."
            )
        except Exception as exc:
            logging.exception("public copy failed")
            db.update_job(job_id, "failed", str(exc))
            extra_hint = ""
            if message.chat.id != message.from_user.id:
                extra_hint = "\n\nIf you sent this from a group, open the bot privately and press Start once so I can DM you."
            await status.edit_text(
                f"Unexpected copy failure while cloning that public post: {exc}{extra_hint}"
            )

    async def start_private_copy_flow(
        message: Message, link: MessageLink, target_chat: int
    ) -> None:
        assert message.from_user is not None
        old_flow = flows.pop(message.from_user.id, None)
        if old_flow and old_flow.client and old_flow.client.is_connected:
            await old_flow.client.disconnect()

        status = await message.reply_text(
            "That is a private group/channel link. I need access before I can clone it.\n\n"
            "Choose one option:",
            reply_markup=private_access_keyboard(),
        )
        flows[message.from_user.id] = PrivateCopyFlow(
            link=link,
            requester_id=message.from_user.id,
            status_chat_id=message.chat.id,
            status_message_id=status.id,
            target_chat=target_chat,
        )

    @bot.on_callback_query(filters.regex("^private_copy:(invite|login|cancel)$"))
    async def private_copy_callback(_: Client, callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        flow = flows.get(user_id)
        action = (callback.data or "").split(":")[-1]

        if action == "cancel":
            flow = flows.pop(user_id, None)
            if flow and flow.client and flow.client.is_connected:
                await flow.client.disconnect()
            await callback.answer("Cancelled.")
            if callback.message:
                await callback.message.edit_text("Cancelled the private copy flow.")
            return

        if not flow:
            await callback.answer(
                "No active private copy flow. Send /copy again.", show_alert=True
            )
            return

        if action == "invite":
            await callback.answer("Waiting for an invite link...")
            flow.step = "invite"
            await send_flow_prompt(
                callback,
                "Send the private group/channel invite link here.\n\n"
                "I will join it with the configured DEFAULT_USER_SESSION_STRING, clone the linked message, "
                "and send the result to your private chat.",
            )
        elif action == "login":
            flow.step = "phone"
            await callback.answer("Opening secure login flow...")
            if callback.message:
                await callback.message.edit_text(
                    "Preparing a temporary member login session...\n\n"
                    "I will ask for the phone number in private chat if needed."
                )
            try:
                flow.client = sessions.new_ephemeral_client(f"member_{user_id}")
                await flow.client.connect()
            except Exception as exc:
                flows.pop(user_id, None)
                await send_flow_prompt(
                    callback,
                    f"I could not start a temporary login session: {exc}\n\n"
                    "Try the invite-link option instead, or try again later.",
                )
                return
            await send_flow_prompt(
                callback,
                "Send the phone number for a user account that is already in that private chat.\n\n"
                "Use international format, for example +15551234567. The login code will be entered with "
                "buttons and will never be sent as a chat message.",
            )

    @bot.on_callback_query(
        filters.regex(r"^private_copy_code:(digit|backspace|clear|submit|cancel)(?::\d)?$")
    )
    async def private_copy_code_callback(_: Client, callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        flow = flows.get(user_id)
        if not flow or flow.step != "code":
            await callback.answer(
                "No active code entry. Send /copy again and choose Login.",
                show_alert=True,
            )
            return

        parts = (callback.data or "").split(":")
        action = parts[1]
        value = parts[2] if len(parts) == 3 else ""
        if action == "cancel":
            await callback.answer("Cancelled.")
            await cleanup_flow(user_id)
            if callback.message:
                await callback.message.edit_text("Cancelled the private copy flow.")
            return
        if action == "digit" and value:
            if len(flow.login_code) < 6:
                flow.login_code += value
        elif action == "backspace":
            flow.login_code = flow.login_code[:-1]
        elif action == "clear":
            flow.login_code = ""
        elif action == "submit":
            if not flow.login_code:
                await callback.answer("Enter the code first.", show_alert=True)
                return
            await callback.answer("Checking code...")
            try:
                await handle_login_code(
                    callback.message.chat.id if callback.message else user_id, flow
                )
            except PhoneCodeInvalid:
                flow.login_code = ""
                if callback.message:
                    await callback.message.edit_text(
                        "That login code was invalid. Enter the latest code.",
                        reply_markup=login_code_keyboard(flow.login_code),
                    )
            except PhoneCodeExpired:
                await cleanup_flow(user_id)
                if callback.message:
                    await callback.message.edit_text(
                        "That login code expired. Send /copy again and choose Login."
                    )
            except SessionPasswordNeeded:
                flow.step = "password"
                if callback.message:
                    await callback.message.edit_text(
                        "2FA is enabled. Send the password here, then I will delete that message when Telegram allows it."
                    )
            except Exception as exc:
                logging.exception("private login code flow failed")
                await cleanup_flow(user_id)
                if callback.message:
                    await callback.message.edit_text(f"Unexpected login failure: {exc}")
            return

        if callback.message:
            await callback.message.edit_text(
                f"Enter the Telegram login code with the buttons below.\n\nCode: {flow.login_code or '—'}",
                reply_markup=login_code_keyboard(flow.login_code),
            )
        await callback.answer()

    @bot.on_message(
        filters.private
        & filters.text
        & ~filters.command(["start", "help", "copy", "cancel"])
    )
    async def private_flow_text_handler(_: Client, message: Message) -> None:
        if not message.from_user:
            return
        flow = flows.get(message.from_user.id)
        if not flow:
            return

        text = (message.text or "").strip()
        try:
            if flow.step == "invite":
                await handle_invite_link(message, flow, text)
            elif flow.step == "phone":
                await safe_delete(message)
                await handle_login_phone(message, flow, text)
            elif flow.step == "code":
                await message.reply_text(
                    "Use the buttons in the login-code prompt. The code must not be sent as a message."
                )
            elif flow.step == "password":
                await safe_delete(message)
                await handle_login_password(message, flow, text)
        except PhoneCodeInvalid:
            await bot.send_message(
                message.chat.id,
                "That login code was invalid. Send the latest code again.",
            )
        except PhoneCodeExpired:
            await cleanup_flow(message.from_user.id)
            await bot.send_message(
                message.chat.id,
                "That login code expired. Send /copy again and choose Login.",
            )
        except PasswordHashInvalid:
            await bot.send_message(
                message.chat.id,
                "That 2FA password was invalid. Try again or send /cancel.",
            )
        except ValueError as exc:
            await bot.send_message(message.chat.id, str(exc))
        except BadRequest as exc:
            await bot.send_message(
                message.chat.id, f"Telegram rejected that step: {exc}"
            )
        except CopyError as exc:
            await cleanup_flow(message.from_user.id)
            await bot.send_message(
                message.chat.id, f"I could not clone the private message.\n\n{exc}"
            )
        except Exception as exc:
            logging.exception("private copy flow failed")
            await cleanup_flow(message.from_user.id)
            await bot.send_message(
                message.chat.id,
                f"Unexpected private-copy failure: {exc}\n\nSend /copy again to restart cleanly.",
            )

    async def handle_invite_link(
        message: Message, flow: PrivateCopyFlow, invite_link: str
    ) -> None:
        if not looks_like_invite(invite_link):
            await message.reply_text(
                "That does not look like a Telegram invite link. Send a t.me/+... or t.me/joinchat/... link."
            )
            return

        try:
            user = await sessions.ensure_started(settings.default_user_session)
        except SessionUnavailable as exc:
            raise CopyError(
                "Invite-link access needs DEFAULT_USER_SESSION_STRING. Add it to .env and restart the bot, "
                "or choose Login and provide a member account."
            ) from exc

        progress = await message.reply_text("Joining with the default session...")
        try:
            chat = await user.join_chat(invite_link)
            await progress.edit_text(
                f"Joined {chat.title}. Cloning the linked message..."
            )
        except Exception as exc:
            await progress.edit_text(
                f"The default session could not join with that invite: {exc}\n\n"
                "I will still try to clone in case the default account is already a member."
            )

        detail = await clone_with_client(
            bot, user, flow.link, flow.target_chat, settings.download_dir
        )
        await progress.edit_text(f"Done. Sent to your private chat. {detail}")
        flows.pop(flow.requester_id, None)

    async def handle_login_phone(
        message: Message, flow: PrivateCopyFlow, phone: str
    ) -> None:
        flow.phone = normalize_phone(phone)
        assert flow.client is not None
        sent = await flow.client.send_code(flow.phone)
        flow.phone_code_hash = sent.phone_code_hash
        flow.step = "code"
        flow.login_code = ""
        await bot.send_message(
            message.chat.id,
            "Code sent. Enter it with the buttons below; do not send it as a message.",
            reply_markup=login_code_keyboard(flow.login_code),
        )

    async def handle_login_code(
        chat_id: int, flow: PrivateCopyFlow
    ) -> None:
        code = flow.login_code
        if not code:
            raise ValueError("Enter the numeric Telegram login code with the buttons.")
        assert flow.client is not None and flow.phone and flow.phone_code_hash
        await flow.client.sign_in(flow.phone, flow.phone_code_hash, code)
        await finish_member_login(chat_id, flow)

    async def handle_login_password(
        message: Message, flow: PrivateCopyFlow, password: str
    ) -> None:
        assert flow.client is not None
        await flow.client.check_password(password)
        await finish_member_login(message.chat.id, flow)

    async def finish_member_login(chat_id: int, flow: PrivateCopyFlow) -> None:
        assert flow.client is not None
        me = await flow.client.get_me()
        try:
            flow.session_string = await flow.client.export_session_string()
        except Exception:
            flow.session_string = None

        progress = await bot.send_message(
            chat_id,
            f"Logged in as {me.first_name}. Cloning the private message...",
        )
        detail = await clone_with_client(
            bot, flow.client, flow.link, flow.target_chat, settings.download_dir
        )
        await progress.edit_text(f"Done. Sent to your private chat. {detail}")
        await cleanup_flow(flow.requester_id)

    async def cleanup_flow(user_id: int) -> None:
        flow = flows.pop(user_id, None)
        if flow and flow.client and flow.client.is_connected:
            await flow.client.disconnect()

    async def send_flow_prompt(callback: CallbackQuery, text: str) -> None:
        if callback.message and callback.message.chat.id == callback.from_user.id:
            await callback.message.edit_text(text)
            return

        try:
            await bot.send_message(callback.from_user.id, text)
        except Exception:
            if callback.message:
                await callback.message.edit_text(
                    "I need to continue this privately. Open this bot in private chat, press Start, then send /copy again."
                )
            return

        if callback.message:
            await callback.message.edit_text(
                "I sent you a private prompt to continue this copy flow."
            )

    async def safe_delete(message: Message) -> None:
        try:
            await message.delete()
        except Exception:
            pass

    async def main() -> None:
        await bot.start()
        await sessions.start_saved()
        me = await bot.get_me()
        logging.info("ForwardBot started as @%s", me.username)
        try:
            await asyncio.Event().wait()
        finally:
            for user_id in list(flows):
                await cleanup_flow(user_id)
            await sessions.stop_all()
            await bot.stop()

    try:
        loop.run_until_complete(main())
    finally:
        loop.close()


def private_access_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Use invite link", callback_data="private_copy:invite"
                ),
                InlineKeyboardButton(
                    "Login member account", callback_data="private_copy:login"
                ),
            ],
            [InlineKeyboardButton("Cancel", callback_data="private_copy:cancel")],
        ]
    )


def login_code_keyboard(code: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                digit, callback_data=f"private_copy_code:digit:{digit}"
            )
            for digit in row
        ]
        for row in ("123", "456", "789")
    ]
    rows.append(
        [
            InlineKeyboardButton("0", callback_data="private_copy_code:digit:0"),
            InlineKeyboardButton("Backspace", callback_data="private_copy_code:backspace"),
            InlineKeyboardButton("Clear", callback_data="private_copy_code:clear"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                f"Enter {code or 'code'}", callback_data="private_copy_code:submit"
            ),
            InlineKeyboardButton("Cancel", callback_data="private_copy_code:cancel"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def normalize_phone(value: str) -> str:
    value = value.strip().replace(" ", "")
    if not value.startswith("+"):
        raise ValueError("Phone number must start with + and country code.")
    return value


def looks_like_invite(value: str) -> bool:
    value = value.strip()
    return bool(
        re.match(r"^(?:https?://)?t\.me/(?:\+|joinchat/)[A-Za-z0-9_-]+$", value)
        or re.match(
            r"^(?:https?://)?telegram\.me/(?:\+|joinchat/)[A-Za-z0-9_-]+$", value
        )
    )
