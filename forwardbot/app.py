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
        await message.reply_text("/copy MESSAGE_LINK ကို ပို့ပါ။\n\n")

    @bot.on_message(filters.command("cancel"))
    async def cancel_handler(_: Client, message: Message) -> None:
        flow = flows.pop(message.from_user.id, None) if message.from_user else None
        if flow and flow.client and flow.client.is_connected:
            await flow.client.disconnect()
        await message.reply_text("လက်ရှိ copy လုပ်နေမှုကို ပယ်ဖျက်လိုက်ပါပြီ။")

    @bot.on_message(filters.command("copy"))
    async def copy_handler(_: Client, message: Message) -> None:
        if not message.from_user:
            await message.reply_text(
                "ရလဒ်ကို DM ပို့နိုင်ရန် user sender ကို မြင်ရပါမည်။"
            )
            return

        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.reply_text("အသုံးပြုပုံ - /copy MESSAGE_LINK")
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
        status = await message.reply_text("အများမြင် post ကို ကူးယူနေပါသည်...")
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
            await status.edit_text(
                f"ပြီးပါပြီ။ သင့် private chat သို့ ပို့ပြီးပါပြီ။ {detail}"
            )
        except CopyError as exc:
            db.update_job(job_id, "failed", str(exc))
            await status.edit_text(
                f"ထိုအများမြင် post ကို ကူးယူ၍ မရပါ။\n\n{exc}\n\n"
                "DEFAULT_USER_SESSION_STRING ကို သတ်မှတ်ထားခြင်းရှိ၊ လင့်ခ်ပါ post ကို ထို account က မြင်နိုင်ခြင်းရှိ စစ်ဆေးပါ။"
            )
        except Exception as exc:
            logging.exception("public copy failed")
            db.update_job(job_id, "failed", str(exc))
            extra_hint = ""
            if message.chat.id != message.from_user.id:
                extra_hint = "\n\nGroup မှ ပို့ခဲ့ပါက bot ကို private chat တွင်ဖွင့်ပြီး Start ကို တစ်ကြိမ်နှိပ်ပါ။ ထို့နောက် DM ပို့နိုင်ပါမည်။"
            await status.edit_text(
                f"အများမြင် post ကို ကူးယူရာတွင် မမျှော်လင့်ထားသော အမှားဖြစ်ပွားခဲ့သည်: {exc}{extra_hint}"
            )

    async def start_private_copy_flow(
        message: Message, link: MessageLink, target_chat: int
    ) -> None:
        assert message.from_user is not None
        old_flow = flows.pop(message.from_user.id, None)
        if old_flow and old_flow.client and old_flow.client.is_connected:
            await old_flow.client.disconnect()

        status = await message.reply_text(
            "ဤလင့်ခ်သည် private group/channel ဖြစ်ပါသည်။ ကူးယူရန် ဝင်ရောက်ခွင့်လိုအပ်ပါသည်။\n\n"
            "နည်းလမ်းတစ်ခု ရွေးပါ -",
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
            await callback.answer("ပယ်ဖျက်လိုက်ပါပြီ။")
            if callback.message:
                await callback.message.edit_text(
                    "Private copy လုပ်နေမှုကို ပယ်ဖျက်လိုက်ပါပြီ။"
                )
            return

        if not flow:
            await callback.answer(
                "လက်ရှိ private copy လုပ်နေမှု မရှိပါ။ /copy ကို ထပ်ပို့ပါ။",
                show_alert=True,
            )
            return

        if action == "invite":
            await callback.answer("Invite link ကို စောင့်နေပါသည်...")
            flow.step = "invite"
            await send_flow_prompt(
                callback, "Private group/channel ၏ invite link ကို ပို့ပါ။\n\n"
            )
        elif action == "login":
            flow.step = "phone"
            await callback.answer("လုံခြုံသော login လုပ်ငန်းစဉ်ကို ဖွင့်နေပါသည်...")
            if callback.message:
                await callback.message.edit_text(
                    "ယာယီ member login session ကို ပြင်ဆင်နေပါသည်...\n\n"
                    "လိုအပ်ပါက phone number ကို private chat တွင် မေးပါမည်။"
                )
            try:
                flow.client = sessions.new_ephemeral_client(f"member_{user_id}")
                await flow.client.connect()
            except Exception as exc:
                flows.pop(user_id, None)
                await send_flow_prompt(
                    callback,
                    f"ယာယီ login session စတင်၍ မရပါ: {exc}\n\n"
                    "Invite-link နည်းလမ်းကို စမ်းပါ၊ သို့မဟုတ် နောက်မှ ထပ်စမ်းပါ။",
                )
                return
            await send_flow_prompt(
                callback,
                "ထို private chat ထဲတွင် ရှိပြီးသား user account ၏ phone number ကို ပို့ပါ။\n\n"
                "နိုင်ငံကုဒ်ပါသော ပုံစံကို သုံးပါ၊ ဥပမာ +15551234567။",
            )

    @bot.on_callback_query(
        filters.regex(
            r"^private_copy_code:(digit|backspace|clear|submit|cancel)(?::\d)?$"
        )
    )
    async def private_copy_code_callback(_: Client, callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        flow = flows.get(user_id)
        if not flow or flow.step != "code":
            await callback.answer(
                "လက်ရှိ code ထည့်နေမှု မရှိပါ။ /copy ကို ထပ်ပို့ပြီး Login ကို ရွေးပါ။",
                show_alert=True,
            )
            return

        parts = (callback.data or "").split(":")
        action = parts[1]
        value = parts[2] if len(parts) == 3 else ""
        if action == "cancel":
            await callback.answer("ပယ်ဖျက်လိုက်ပါပြီ။")
            await cleanup_flow(user_id)
            if callback.message:
                await callback.message.edit_text(
                    "Private copy လုပ်နေမှုကို ပယ်ဖျက်လိုက်ပါပြီ။"
                )
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
                await callback.answer("Code ကို အရင်ထည့်ပါ။", show_alert=True)
                return
            await callback.answer("Code ကို စစ်ဆေးနေပါသည်...")
            try:
                await handle_login_code(
                    callback.message.chat.id if callback.message else user_id, flow
                )
            except PhoneCodeInvalid:
                flow.login_code = ""
                if callback.message:
                    await callback.message.edit_text(
                        "Login code မှားနေပါသည်။ နောက်ဆုံးရ code ကို ထည့်ပါ။",
                        reply_markup=login_code_keyboard(flow.login_code),
                    )
            except PhoneCodeExpired:
                await cleanup_flow(user_id)
                if callback.message:
                    await callback.message.edit_text(
                        "Login code သက်တမ်းကုန်သွားပါပြီ။ /copy ကို ထပ်ပို့ပြီး Login ကို ရွေးပါ။"
                    )
            except SessionPasswordNeeded:
                flow.step = "password"
                if callback.message:
                    await callback.message.edit_text(
                        "2FA ဖွင့်ထားပါသည်။ Password ကို ဤနေရာတွင် ပို့ပါ။ Telegram ခွင့်ပြုပါက ထို message ကို ဖျက်ပေးပါမည်။"
                    )
            except Exception as exc:
                logging.exception("private login code flow failed")
                await cleanup_flow(user_id)
                if callback.message:
                    await callback.message.edit_text(
                        f"Login လုပ်ရာတွင် မမျှော်လင့်ထားသော အမှားဖြစ်ပွားခဲ့သည်: {exc}"
                    )
            return

        if callback.message:
            await callback.message.edit_text(
                f"အောက်ပါခလုတ်များဖြင့် Telegram login code ကို ထည့်ပါ။\n\nCode: {flow.login_code or '—'}",
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
                    "Login-code prompt ထဲရှိ ခလုတ်များကို သုံးပါ။ Code ကို message အဖြစ် မပို့ပါနှင့်။"
                )
            elif flow.step == "password":
                await safe_delete(message)
                await handle_login_password(message, flow, text)
        except PhoneCodeInvalid:
            await bot.send_message(
                message.chat.id,
                "Login code မှားနေပါသည်။ နောက်ဆုံးရ code ကို ထပ်ပို့ပါ။",
            )
        except PhoneCodeExpired:
            await cleanup_flow(message.from_user.id)
            await bot.send_message(
                message.chat.id,
                "Login code သက်တမ်းကုန်သွားပါပြီ။ /copy ကို ထပ်ပို့ပြီး Login ကို ရွေးပါ။",
            )
        except PasswordHashInvalid:
            await bot.send_message(
                message.chat.id,
                "2FA password မှားနေပါသည်။ ထပ်စမ်းပါ၊ သို့မဟုတ် /cancel ကို ပို့ပါ။",
            )
        except ValueError as exc:
            await bot.send_message(message.chat.id, str(exc))
        except BadRequest as exc:
            await bot.send_message(
                message.chat.id, f"ထိုအဆင့်ကို Telegram က လက်မခံပါ: {exc}"
            )
        except CopyError as exc:
            await cleanup_flow(message.from_user.id)
            await bot.send_message(
                message.chat.id, f"Private message ကို ကူးယူ၍ မရပါ။\n\n{exc}"
            )
        except Exception as exc:
            logging.exception("private copy flow failed")
            await cleanup_flow(message.from_user.id)
            await bot.send_message(
                message.chat.id,
                f"Private copy လုပ်ရာတွင် မမျှော်လင့်ထားသော အမှားဖြစ်ပွားခဲ့သည်: {exc}\n\nအစမှ ပြန်စရန် /copy ကို ထပ်ပို့ပါ။",
            )

    async def handle_invite_link(
        message: Message, flow: PrivateCopyFlow, invite_link: str
    ) -> None:
        if not looks_like_invite(invite_link):
            await message.reply_text(
                "ဤလင့်ခ်သည် Telegram invite link မဟုတ်သလို ဖြစ်နေပါသည်။ t.me/+... သို့မဟုတ် t.me/joinchat/... link ကို ပို့ပါ။"
            )
            return

        try:
            user = await sessions.ensure_started(settings.default_user_session)
        except SessionUnavailable as exc:
            raise CopyError(
                "Invite-link ဖြင့် ဝင်ရောက်ရန် DEFAULT_USER_SESSION_STRING လိုအပ်ပါသည်။ .env ထဲတွင် ထည့်ပြီး bot ကို restart လုပ်ပါ၊ "
                "သို့မဟုတ် Login ကို ရွေးပြီး member account ကို အသုံးပြုပါ။"
            ) from exc

        progress = await message.reply_text("မူလ session ဖြင့် ဝင်ရောက်နေပါသည်...")
        try:
            chat = await user.join_chat(invite_link)
            await progress.edit_text(
                f"{chat.title} ထဲသို့ ဝင်ရောက်ပြီးပါပြီ။ လင့်ခ်ပါ message ကို ကူးယူနေပါသည်..."
            )
        except Exception as exc:
            await progress.edit_text(
                f"ထို invite ဖြင့် မူလ session က ဝင်ရောက်၍ မရပါ: {exc}\n\n"
                "မူလ account သည် ထို group ၏ member ဖြစ်ပြီးသားဖြစ်နိုင်သောကြောင့် ကူးယူရန် ဆက်လက်ကြိုးစားပါမည်။"
            )

        detail = await clone_with_client(
            bot, user, flow.link, flow.target_chat, settings.download_dir
        )
        await progress.edit_text(
            f"ပြီးပါပြီ။ သင့် private chat သို့ ပို့ပြီးပါပြီ။ {detail}"
        )
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
            "Code ပို့ပြီးပါပြီ။ အောက်ပါခလုတ်များဖြင့် ထည့်ပါ။ Code ကို message အဖြစ် မပို့ပါနှင့်။",
            reply_markup=login_code_keyboard(flow.login_code),
        )

    async def handle_login_code(chat_id: int, flow: PrivateCopyFlow) -> None:
        code = flow.login_code
        if not code:
            raise ValueError("ခလုတ်များဖြင့် ဂဏန်းပါ Telegram login code ကို ထည့်ပါ။")
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
            f"{me.first_name} အဖြစ် login ဝင်ပြီးပါပြီ။ Private message ကို ကူးယူနေပါသည်...",
        )
        detail = await clone_with_client(
            bot, flow.client, flow.link, flow.target_chat, settings.download_dir
        )
        await progress.edit_text(
            f"ပြီးပါပြီ။ သင့် private chat သို့ ပို့ပြီးပါပြီ။ {detail}"
        )
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
                    "ဆက်လက်လုပ်ဆောင်ရန် private chat လိုအပ်ပါသည်။ Bot ကို private chat တွင်ဖွင့်ပြီး Start ကို နှိပ်ကာ /copy ကို ထပ်ပို့ပါ။"
                )
            return

        if callback.message:
            await callback.message.edit_text(
                "ဤ copy လုပ်ငန်းစဉ်ကို ဆက်လုပ်ရန် private prompt ကို ပို့ပေးလိုက်ပါပြီ။"
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
                    "Invite link သုံးမည်", callback_data="private_copy:invite"
                ),
                InlineKeyboardButton(
                    "Member account ဖြင့် Login", callback_data="private_copy:login"
                ),
            ],
            [InlineKeyboardButton("ပယ်ဖျက်မည်", callback_data="private_copy:cancel")],
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
            InlineKeyboardButton(
                "နောက်သို့ဖျက်", callback_data="private_copy_code:backspace"
            ),
            InlineKeyboardButton("ရှင်းမည်", callback_data="private_copy_code:clear"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                f"{code or 'Code'} ထည့်မည်", callback_data="private_copy_code:submit"
            ),
            InlineKeyboardButton(
                "ပယ်ဖျက်မည်", callback_data="private_copy_code:cancel"
            ),
        ]
    )
    return InlineKeyboardMarkup(rows)


def normalize_phone(value: str) -> str:
    value = value.strip().replace(" ", "")
    if not value.startswith("+"):
        raise ValueError("Phone number သည် + နှင့် country code ဖြင့် စရပါမည်။")
    return value


def looks_like_invite(value: str) -> bool:
    value = value.strip()
    return bool(
        re.match(r"^(?:https?://)?t\.me/(?:\+|joinchat/)[A-Za-z0-9_-]+$", value)
        or re.match(
            r"^(?:https?://)?telegram\.me/(?:\+|joinchat/)[A-Za-z0-9_-]+$", value
        )
    )
