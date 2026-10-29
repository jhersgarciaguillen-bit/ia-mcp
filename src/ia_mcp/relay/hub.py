from __future__ import annotations

import asyncio
import hmac
import json
import logging
from typing import Any

from ia_mcp.agent.protocol import ACTIONS, valid_request_id

LOGGER = logging.getLogger("ia_mcp.relay")
MAX_MESSAGE_CHARS = 2_000_000


class RelayHub:
    def __init__(self, agent_token: str, client_token: str) -> None:
        self._agent_token = agent_token
        self._client_token = client_token
        self._agent: Any | None = None
        self._agent_id: str | None = None
        self._clients: set[Any] = set()
        self._pending: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    @property
    def agent_online(self) -> bool:
        return self._agent is not None

    def authorize(self, role: str, token: Any) -> bool:
        if not isinstance(token, str):
            return False
        expected = self._agent_token if role == "agent" else self._client_token
        try:
            return hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8"))
        except (AttributeError, UnicodeError):
            return False

    async def agent_connected(self, websocket: Any, agent_id: Any) -> None:
        name = agent_id if isinstance(agent_id, str) else None
        async with self._lock:
            previous = self._agent
            self._agent = websocket
            self._agent_id = name[:80] if name else None
            pending = (
                list(self._pending.items())
                if previous is not None and previous is not websocket
                else []
            )
            self._pending.clear()
        if previous is not None and previous is not websocket:
            await _safe_close(previous, 4001, "Reemplazado por otro agente.")
        for request_id, client in pending:
            await _safe_send(
                client,
                {
                    "type": "error",
                    "id": request_id,
                    "error": "El agente local fue reemplazado.",
                },
            )
        await self.broadcast(
            {"type": "agent_online", "agent_id": self._agent_id or "Agente local"}
        )

    async def agent_disconnected(self, websocket: Any) -> None:
        async with self._lock:
            if self._agent is not websocket:
                return
            self._agent = None
            self._agent_id = None
            pending = list(self._pending.items())
            self._pending.clear()
        for request_id, client in pending:
            await _safe_send(
                client,
                {
                    "type": "error",
                    "id": request_id,
                    "error": "El agente local se desconecto.",
                },
            )
        await self.broadcast({"type": "agent_offline"})

    async def client_connected(self, websocket: Any) -> None:
        async with self._lock:
            self._clients.add(websocket)

    async def client_disconnected(self, websocket: Any) -> None:
        async with self._lock:
            self._clients.discard(websocket)
            stale = [
                request_id
                for request_id, client in self._pending.items()
                if client is websocket
            ]
            for request_id in stale:
                self._pending.pop(request_id, None)

    async def client_message(self, websocket: Any, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            await self._send_error(websocket, None, "La solicitud no es JSON valido.")
            return
        if not isinstance(data, dict) or data.get("type") != "request":
            await self._send_error(websocket, None, "Solicitud remota invalida.")
            return
        request_id = data.get("id")
        action = data.get("action")
        payload = data.get("payload", {})
        if not valid_request_id(request_id):
            await self._send_error(websocket, None, "Identificador de solicitud invalido.")
            return
        if not isinstance(action, str) or action not in ACTIONS:
            await self._send_error(websocket, request_id, "Accion no permitida.")
            return
        if not isinstance(payload, dict):
            await self._send_error(websocket, request_id, "Contenido invalido.")
            return
        if len(raw) > MAX_MESSAGE_CHARS:
            await self._send_error(websocket, request_id, "Solicitud demasiado grande.")
            return

        async with self._lock:
            agent = self._agent
            if agent is not None:
                self._pending[request_id] = websocket

        if agent is None:
            await self._send_error(
                websocket, request_id, "El agente local no esta conectado."
            )
            return

        try:
            await agent.send_text(raw)
        except Exception:
            async with self._lock:
                self._pending.pop(request_id, None)
            await self._send_error(
                websocket, request_id, "No se pudo enviar la solicitud al agente."
            )

    async def agent_message(self, websocket: Any, raw: str) -> None:
        if len(raw) > MAX_MESSAGE_CHARS:
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(data, dict):
            return
        if data.get("type") not in {"event", "result", "error"}:
            return
        request_id = data.get("id")
        if not valid_request_id(request_id):
            return
        async with self._lock:
            if self._agent is not websocket:
                return
            target = self._pending.get(request_id)
            if target is not None and data.get("type") in {"result", "error"}:
                self._pending.pop(request_id, None)
        if target is None:
            return
        await _safe_send(target, data)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            clients = list(self._clients)
        if clients:
            await asyncio.gather(
                *(_safe_send(client, payload) for client in clients),
                return_exceptions=True,
            )

    async def _send_error(
        self,
        websocket: Any,
        request_id: str | None,
        message: str,
    ) -> None:
        payload: dict[str, Any] = {"type": "error", "error": message}
        if request_id:
            payload["id"] = request_id
        await _safe_send(websocket, payload)

    async def close(self) -> None:
        async with self._lock:
            clients = list(self._clients)
            agent = self._agent
            self._clients.clear()
            self._agent = None
            self._pending.clear()
        for client in clients:
            await _safe_close(client, 1001, "Relay detenido.")
        if agent is not None:
            await _safe_close(agent, 1001, "Relay detenido.")


async def _safe_send(websocket: Any, payload: dict[str, Any]) -> None:
    try:
        await websocket.send_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )
    except Exception:
        pass


async def _safe_close(websocket: Any, code: int, reason: str) -> None:
    try:
        await websocket.close(code=code, reason=reason)
    except Exception:
        pass
