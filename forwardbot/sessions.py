from __future__ import annotations

import asyncio
import logging

from pyrogram import Client

from .config import Settings
from .db import Database

log = logging.getLogger(__name__)


class SessionUnavailable(Exception):
    pass


class SessionManager:
    def __init__(self, settings: Settings, db: Database, loop: asyncio.AbstractEventLoop) -> None:
        self.settings = settings
        self.db = db
        self.loop = loop
        self.settings.session_dir.mkdir(parents=True, exist_ok=True)
        self._clients: dict[str, Client] = {}

    def path_name(self, name: str) -> str:
        safe_name = "".join(ch for ch in name if ch.isalnum() or ch in ("_", "-"))
        if not safe_name:
            raise ValueError("Session name must contain letters, numbers, dash, or underscore.")
        return safe_name

    def session_file(self, name: str) -> str:
        return str(self.settings.session_dir / f"{self.path_name(name)}.session")

    def has_session_material(self, name: str) -> bool:
        if (
            name == self.settings.default_user_session
            and self.settings.default_user_session_string
        ):
            return True
        return self.settings.session_dir.joinpath(f"{self.path_name(name)}.session").exists()

    def new_client(self, name: str) -> Client:
        session_string = None
        if (
            name == self.settings.default_user_session
            and self.settings.default_user_session_string
        ):
            session_string = self.settings.default_user_session_string

        return Client(
            self.path_name(name),
            api_id=self.settings.api_id,
            api_hash=self.settings.api_hash,
            workdir=str(self.settings.session_dir),
            session_string=session_string,
            in_memory=bool(session_string),
            loop=self.loop,
        )

    def new_ephemeral_client(self, name: str) -> Client:
        return Client(
            self.path_name(name),
            api_id=self.settings.api_id,
            api_hash=self.settings.api_hash,
            in_memory=True,
            loop=self.loop,
        )

    async def start_saved(self) -> None:
        if self.settings.default_user_session_string:
            try:
                await self.ensure_started(self.settings.default_user_session)
            except Exception as exc:
                log.warning(
                    "Could not start DEFAULT_USER_SESSION_STRING for %s: %s",
                    self.settings.default_user_session,
                    exc,
                )

        for session in self.db.list_sessions():
            try:
                await self.ensure_started(session.name)
            except Exception as exc:
                log.warning("Could not start saved session %s: %s", session.name, exc)

    async def ensure_started(self, name: str) -> Client:
        existing = self._clients.get(name)
        if existing and existing.is_connected:
            return existing

        if not self.has_session_material(name):
            raise SessionUnavailable(
                f"Session `{name}` is not available. Set DEFAULT_USER_SESSION_STRING or use the Login button."
            )

        client = self.new_client(name)
        await client.start()
        self._clients[name] = client
        return client

    async def stop_all(self) -> None:
        for client in list(self._clients.values()):
            if client.is_connected:
                await client.stop()
        self._clients.clear()

    async def replace_started(self, name: str, client: Client) -> Client:
        old = self._clients.get(name)
        if old and old.is_connected:
            await old.stop()
        self._clients[name] = client
        return client
