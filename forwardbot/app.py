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
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from .config import Settings
from .copier import CopyError, copy_message
from .db import Database
from .links import parse_message_link
from .sessions import SessionManager

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


@dataclass
class LoginFlow:
    session_name: str
    step: str
    client: Client | None = None
    phone: str | None = None
    phone_code_hash: str | None = None


def run() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    settings = Settings.load()
    settings.download_dir.mkdir(parents=True, exist_ok=True)
    settings.session_dir.mkdir(parents=True, exist_ok=True)

    db = Database(settings.db_path)
    db.init()
    sessions = SessionManager(settings, db)

    bot = Client(
        "forwardbot",
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        bot_token=settings.bot_token,
        workdir=str(settings.session_dir),
    )
    login_flows: dict[int, LoginFlow] = {}

    def is_owner(message: Message) -> bool:
        return bool(message.from_user and message.from_user.id in settings.owner_ids)

    async def require_owner(message: Message) -> bool:
        if is_owner(message):
            return True
        await message.reply_text("This bot is owner-only. Ask the operator to add your Telegram ID.")
        return False

    @bot.on_message(filters.command(["start", "help"]))
    async def start_handler(_: Client, message: Message) -> None:
        await message.reply_text(
            "Send /copy <message-link> to copy a Telegram message here.\n\n"
            "Owner tools:\n"
            "/login [session-name] - login a user session\n"
            "/join <invite-link> [session-name] - join a private source\n"
            "/sessions - list sessions\n"
            "/cancel - cancel the current flow"
        )

    @bot.on_message(filters.command("sessions"))
    async def sessions_handler(_: Client, message: Message) -> None:
        if not await require_owner(message):
            return
        rows = db.list_sessions()
        if not rows:
            await message.reply_text("No user sessions yet. Run /login first.")
            return
        text = "\n".join(
            f"- {row.name} (owner {row.owner_id}, phone {row.phone or 'hidden'})"
            for row in rows
        )
        await message.reply_text(f"Known sessions:\n{text}")

    @bot.on_message(filters.command("cancel"))
    async def cancel_handler(_: Client, message: Message) -> None:
        flow = login_flows.pop(message.chat.id, None)
        if flow and flow.client and flow.client.is_connected:
            await flow.client.disconnect()
        await message.reply_text("Cancelled.")

    @bot.on_message(filters.command("login"))
    async def login_handler(_: Client, message: Message) -> None:
        if not await require_owner(message):
            return
        if message.chat.type.name != "PRIVATE":
            await message.reply_text("Please run /login in a private chat so your code and 2FA prompts stay private.")
            return

        parts = message.text.split(maxsplit=1) if message.text else []
        session_name = parts[1].strip() if len(parts) > 1 else settings.default_user_session
        try:
            client = sessions.new_client(session_name)
            await client.connect()
            try:
                me = await client.get_me()
            except Exception:
                me = None
            if me:
                await client.disconnect()
                db.upsert_session(session_name, message.from_user.id, None)
                started = await sessions.ensure_started(session_name)
                me = await started.get_me()
                await message.reply_text(
                    f"Session `{session_name}` is already ready as {me.first_name} (`{me.id}`)."
                )
                return
        except Exception as exc:
            await message.reply_text(f"Could not prepare that session: {exc}")
            return

        login_flows[message.chat.id] = LoginFlow(
            session_name=session_name,
            step="phone",
            client=client,
        )
        await message.reply_text(
            f"Logging in session `{session_name}`.\n"
            "Send the phone number in international format, for example `+15551234567`."
        )

    @bot.on_message(filters.command("join"))
    async def join_handler(_: Client, message: Message) -> None:
        if not await require_owner(message):
            return

        parts = (message.text or "").split()
        if len(parts) < 2:
            await message.reply_text("Usage: /join <invite-link> [session-name]")
            return

        invite_link = parts[1].strip()
        session_name = parts[2].strip() if len(parts) > 2 else settings.default_user_session
        try:
            user = await sessions.ensure_started(session_name)
            chat = await user.join_chat(invite_link)
            await message.reply_text(f"Joined `{chat.title}` with session `{session_name}`.")
        except Exception as exc:
            await message.reply_text(
                f"Could not join with `{session_name}`: {exc}\n"
                "If the account is already a member, you can use /copy directly. Otherwise run /login for another account."
            )

    @bot.on_message(filters.command("copy"))
    async def copy_handler(_: Client, message: Message) -> None:
        if not await require_owner(message):
            return
        parts = (message.text or "").split(maxsplit=2)
        if len(parts) < 2:
            await message.reply_text("Usage: /copy <message-link> [target-chat-id]")
            return

        target_chat = parts[2].strip() if len(parts) > 2 else settings.default_target_chat or message.chat.id
        try:
            link = parse_message_link(parts[1])
        except ValueError as exc:
            await message.reply_text(str(exc))
            return

        job_id = db.create_job(message.from_user.id, link.raw, str(target_chat))
        status = await message.reply_text("Working on it...")
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
            await status.edit_text(f"Done. {detail}")
        except CopyError as exc:
            db.update_job(job_id, "needs_action" if exc.needs_private_help else "failed", str(exc))
            markup = None
            if exc.needs_private_help:
                markup = InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("Login member account", callback_data="login_default")],
                    ]
                )
            await status.edit_text(str(exc), reply_markup=markup)
        except Exception as exc:
            logging.exception("copy failed")
            db.update_job(job_id, "failed", str(exc))
            await status.edit_text(f"Copy failed: {exc}")

    @bot.on_callback_query(filters.regex("^login_default$"))
    async def login_callback(client: Client, callback) -> None:
        if callback.from_user.id not in settings.owner_ids:
            await callback.answer("Owner-only action.", show_alert=True)
            return
        await callback.answer()
        await client.send_message(
            callback.from_user.id,
            f"Run /login {settings.default_user_session} here, then retry /copy.",
        )

    @bot.on_message(filters.private & filters.text & ~filters.command(["start", "help", "login", "copy", "sessions", "join", "cancel"]))
    async def login_flow_handler(_: Client, message: Message) -> None:
        if not is_owner(message):
            return
        flow = login_flows.get(message.chat.id)
        if not flow:
            return

        text = (message.text or "").strip()
        try:
            if flow.step == "phone":
                await safe_delete(message)
                flow.phone = normalize_phone(text)
                assert flow.client is not None
                sent = await flow.client.send_code(flow.phone)
                flow.phone_code_hash = sent.phone_code_hash
                flow.step = "code"
                await bot.send_message(
                    message.chat.id,
                    "Code sent. Send the login code here. Spaces are okay.",
                )
            elif flow.step == "code":
                await safe_delete(message)
                code = re.sub(r"\D", "", text)
                assert flow.client is not None and flow.phone and flow.phone_code_hash
                try:
                    await flow.client.sign_in(flow.phone, flow.phone_code_hash, code)
                except SessionPasswordNeeded:
                    flow.step = "password"
                    await bot.send_message(message.chat.id, "2FA is enabled. Send the password.")
                    return
                await finish_login(message, flow)
            elif flow.step == "password":
                await safe_delete(message)
                assert flow.client is not None
                await flow.client.check_password(text)
                await finish_login(message, flow)
        except PhoneCodeInvalid:
            await bot.send_message(message.chat.id, "That code was invalid. Send the latest code again.")
        except PhoneCodeExpired:
            login_flows.pop(message.chat.id, None)
            await bot.send_message(message.chat.id, "That code expired. Run /login again.")
        except PasswordHashInvalid:
            await bot.send_message(message.chat.id, "That 2FA password was invalid. Try again or /cancel.")
        except ValueError as exc:
            await bot.send_message(message.chat.id, str(exc))
        except BadRequest as exc:
            await bot.send_message(message.chat.id, f"Telegram rejected that step: {exc}")
        except Exception as exc:
            logging.exception("login flow failed")
            login_flows.pop(message.chat.id, None)
            await bot.send_message(message.chat.id, f"Login failed: {exc}")

    async def finish_login(message: Message, flow: LoginFlow) -> None:
        assert flow.client is not None
        me = await flow.client.get_me()
        db.upsert_session(flow.session_name, message.from_user.id, flow.phone)
        await flow.client.disconnect()
        started = await sessions.ensure_started(flow.session_name)
        me = await started.get_me()
        login_flows.pop(message.chat.id, None)
        await bot.send_message(
            message.chat.id,
            f"Session `{flow.session_name}` is ready as {me.first_name} (`{me.id}`).",
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
            await sessions.stop_all()
            await bot.stop()

    asyncio.run(main())


def normalize_phone(value: str) -> str:
    value = value.strip().replace(" ", "")
    if not value.startswith("+"):
        raise ValueError("Phone number must start with + and country code.")
    return value
