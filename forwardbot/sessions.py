from __future__ import annotations

import asyncio
import logging

from pyrogram import Client

from .config import Settings
from .db import Database

log = logging.getLogger(__name__)


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

    def new_client(self, name: str) -> Client:
        return Client(
            self.path_name(name),
            api_id=self.settings.api_id,
            api_hash=self.settings.api_hash,
            workdir=str(self.settings.session_dir),
            loop=self.loop,
        )

    async def start_saved(self) -> None:
        for session in self.db.list_sessions():
            try:
                await self.ensure_started(session.name)
            except Exception as exc:
                log.warning("Could not start saved session %s: %s", session.name, exc)

    async def ensure_started(self, name: str) -> Client:
        existing = self._clients.get(name)
        if existing and existing.is_connected:
            return existing

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
