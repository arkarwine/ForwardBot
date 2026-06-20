# ForwardBot

A Kurigram + SQLite Telegram bot that copies messages from Telegram message links and sends them to a target chat, with guided fallback flows when the bot cannot access the source.

## What it handles

- Public links like `https://t.me/channel/123`.
- Private internal links like `https://t.me/c/123456789/123` when an authorized user session can access the chat.
- Bot-accessible messages using `copy_message` first.
- Protected or restricted-save messages by downloading through an authorized user session and re-uploading through the bot.
- Private channel/group cases where the bot is not a member:
  - Join a source via invite link using a user session.
  - Create/login a user session in-chat with phone, code, and 2FA prompts.
  - Register multiple user sessions and choose one for private sources.

Use this only for chats and content you are authorized to access and redistribute.

## Setup

1. Create a Telegram API app at `https://my.telegram.org`.
2. Copy `.env.example` to `.env` and fill `API_ID`, `API_HASH`, `BOT_TOKEN`, and `OWNER_IDS`.
3. Install dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

4. Start the bot:

```powershell
.\.venv\Scripts\python -m forwardbot
```

## Commands

- `/start` - show help.
- `/copy MESSAGE_LINK` - copy/send a public linked message to the chat where the command was used. Owners can also use private internal links.
- `/login [session-name]` - owner-only; create or refresh a user session.
- `/sessions` - owner-only; list known user sessions.
- `/join INVITE_LINK [session-name]` - owner-only; join a private source with a user session.
- `/cancel` - cancel an active login/copy flow.

## Notes

- User login prompts only work in private chat with an owner.
- Non-owners can use `/copy` for public links like `https://t.me/channel/123`. Private `t.me/c/...` links stay owner-only.
- The bot deletes phone/code/password prompt replies when possible.
- For private `t.me/c/...` links, Telegram links do not include enough information for an account that is not already in the chat. Use `/join INVITE_LINK` first or `/login` an account that is already a member.
- Bots cannot bypass Telegram access control. The fallback user session must be a legitimate member or must successfully join via invite.
