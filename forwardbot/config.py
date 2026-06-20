from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _csv_ints(value: str) -> set[int]:
    ids: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if item:
            ids.add(int(item))
    return ids


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str
    bot_token: str
    owner_ids: set[int]
    default_user_session: str
    default_user_session_string: str
    db_path: Path
    session_dir: Path
    download_dir: Path

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv()

        missing = [
            key
            for key in (
                "API_ID",
                "API_HASH",
                "BOT_TOKEN",
                "DEFAULT_USER_SESSION_STRING",
            )
            if not os.getenv(key)
        ]
        if missing:
            raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")

        return cls(
            api_id=int(os.environ["API_ID"]),
            api_hash=os.environ["API_HASH"],
            bot_token=os.environ["BOT_TOKEN"],
            owner_ids=_csv_ints(os.getenv("OWNER_IDS", "")),
            default_user_session=os.getenv("DEFAULT_USER_SESSION", "default").strip()
            or "default",
            default_user_session_string=os.environ[
                "DEFAULT_USER_SESSION_STRING"
            ].strip(),
            db_path=Path(os.getenv("DB_PATH", "data/forwardbot.sqlite3")),
            session_dir=Path(os.getenv("SESSION_DIR", "sessions")),
            download_dir=Path(os.getenv("DOWNLOAD_DIR", "downloads")),
        )
