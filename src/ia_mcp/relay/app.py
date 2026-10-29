from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from ia_mcp.relay.hub import RelayHub

LOGGER = logging.getLogger("ia_mcp.relay")
STATIC_DIR = Path(__file__).resolve().parents[1] / "web" / "static"
HELLO_TIMEOUT = 10
MIN_TOKEN_LENGTH = 16
load_dotenv()


def create_relay_app(
    agent_token: str | None = None,
    client_token: str | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved_agent = (
            agent_token
            if agent_token is not None
            else os.environ.get("IA_MCP_AGENT_TOKEN", "")
        )
        resolved_client = (
            client_token
            if client_token is not None
            else os.environ.get("IA_MCP_RELAY_CLIENT_TOKEN", "")
        )
        if len(resolved_agent) < MIN_TOKEN_LENGTH:
            raise RuntimeError(
                "Configura IA_MCP_AGENT_TOKEN con al menos 16 caracteres."
            )
        if len(resolved_client) < MIN_TOKEN_LENGTH:
            raise RuntimeError(
                "Configura IA_MCP_RELAY_CLIENT_TOKEN con al menos 16 caracteres."
            )
        if resolved_agent == resolved_client:
            raise RuntimeError(
                "El token del agente y el token del cliente deben ser diferentes."
            )
        app.state.hub = RelayHub(resolved_agent, resolved_client)
        try:
            yield
        finally:
            await app.state.hub.close()

    app = FastAPI(title="IA_MCP Relay", version="0.1.0", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/remote")

    @app.get("/remote", include_in_schema=False)
    async def remote() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "relay": "IA_MCP"}

    @app.websocket("/ws/agent")
    async def agent_socket(websocket: WebSocket) -> None:
        hub: RelayHub = app.state.hub
        hello = await _handshake(websocket, hub, "agent")
        if hello is None:
            return
        await hub.agent_connected(websocket, hello.get("agent_id"))
        try:
            await websocket.send_json(
                {"type": "hello", "ok": True, "role": "agent", "agent_online": True}
            )
            while True:
                raw = await websocket.receive_text()
                await hub.agent_message(websocket, raw)
        except WebSocketDisconnect:
            pass
        finally:
            await hub.agent_disconnected(websocket)

    @app.websocket("/ws/client")
    async def client_socket(websocket: WebSocket) -> None:
        hub: RelayHub = app.state.hub
        hello = await _handshake(websocket, hub, "client")
        if hello is None:
            return
        await hub.client_connected(websocket)
        try:
            await websocket.send_json(
                {
                    "type": "hello",
                    "ok": True,
                    "role": "client",
                    "agent_online": hub.agent_online,
                }
            )
            while True:
                raw = await websocket.receive_text()
                await hub.client_message(websocket, raw)
        except WebSocketDisconnect:
            pass
        finally:
            await hub.client_disconnected(websocket)

    return app


async def _handshake(
    websocket: WebSocket,
    hub: RelayHub,
    role: str,
) -> dict[str, Any] | None:
    await websocket.accept()
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=HELLO_TIMEOUT)
        data = json.loads(raw)
    except (asyncio.TimeoutError, WebSocketDisconnect, json.JSONDecodeError):
        await _close(websocket, 4408, "Saludo invalido o tardio.")
        return None
    if (
        not isinstance(data, dict)
        or data.get("type") != "hello"
        or data.get("role") != role
        or not hub.authorize(role, data.get("token"))
    ):
        try:
            await websocket.send_json(
                {"type": "hello", "ok": False, "error": "No autorizado."}
            )
        except Exception:
            pass
        await _close(websocket, 4401, "No autorizado.")
        return None
    return data


async def _close(websocket: WebSocket, code: int, reason: str) -> None:
    try:
        await websocket.close(code=code, reason=reason)
    except Exception:
        pass


app = create_relay_app()


def main() -> None:
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    default_host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    host = os.environ.get("IA_MCP_RELAY_HOST", default_host)
    port = int(
        os.environ.get("IA_MCP_RELAY_PORT") or os.environ.get("PORT") or "8765"
    )
    uvicorn.run("ia_mcp.relay.app:app", host=host, port=port)


if __name__ == "__main__":
    main()
