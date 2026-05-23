"""
broadcaster.py — WebSocket client registry and fan-out broadcast.

No SEC logic, no project imports except stdlib + fastapi.
"""

import json
import logging

from fastapi import WebSocket
from starlette.websockets import WebSocketState

log = logging.getLogger(__name__)

MAX_CLIENTS = 100  # refuse connections beyond this limit


class Broadcaster:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        if len(self._clients) >= MAX_CLIENTS:
            await ws.close(code=1008, reason="connection limit reached")
            log.warning("Rejected connection — limit of %d reached", MAX_CLIENTS)
            return
        await ws.accept()
        self._clients.add(ws)
        log.info("Client connected  (total: %d)", len(self._clients))

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)
        log.info("Client disconnected (total: %d)", len(self._clients))

    async def broadcast(self, payload: dict) -> None:
        """Send payload to all connected clients. Silently drops dead connections."""
        if not self._clients:
            return
        message = json.dumps(payload, default=str)
        dead: set[WebSocket] = set()
        for client in list(self._clients):
            try:
                if client.client_state == WebSocketState.CONNECTED:
                    await client.send_text(message)
                else:
                    dead.add(client)
            except Exception:
                dead.add(client)
        if dead:
            self._clients -= dead
            log.info("Pruned %d dead client(s)", len(dead))

    @property
    def client_count(self) -> int:
        return len(self._clients)
