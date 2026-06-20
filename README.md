# ForwardBot

A Kurigram + SQLite Telegram bot that clones Telegram message links and sends the result to the command sender's private chat.

## Flow

Public link:

```text
user: /copy https://t.me/gemini12pro/159438
bot: reads it with DEFAULT_USER_SESSION_STRING, clones it, and DMs the cloned message back
```

Private link:

```text
user: /copy https://t.me/c/123456789/42
bot: asks for access method
```

Private access options:

- `Use invite link` - the user sends an invite link; the bot joins with the default session, then clones the linked message.
- `Login member account` - the user logs in through a guided Kurigram conversation; the bot uses that temporary member session to clone the linked message.

The standalone `/login` command has intentionally been removed. Login only appears when a private link needs a member account.

## Setup

1. Create a Telegram API app at `https://my.telegram.org`.
2. Copy `.env.example` to `.env`.
3. Fill `API_ID`, `API_HASH`, `BOT_TOKEN`, and `DEFAULT_USER_SESSION_STRING`.
4. Install dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

5. Start the bot:

```powershell
.\.venv\Scripts\python -m forwardbot
```

## Commands

- `/start` - show help.
- `/copy MESSAGE_LINK` - clone a public or private Telegram message link.
- `/cancel` - cancel an active private-link flow.

## Notes

- `/copy` always sends the cloned message to the sender's private chat.
- If `/copy` is used from a group, the sender must open the bot privately and press Start once so the bot can DM them.
- Public links require `DEFAULT_USER_SESSION_STRING`; the bot does not ask for phone numbers in the server CLI.
- Private login flow deletes phone/code/password messages when Telegram allows it.
- Bots and user sessions cannot bypass Telegram access control. The default session or temporary login account must legitimately be able to access the source.
