from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Awaitable, Callable, cast

import httpx
import websockets
from dotenv import load_dotenv
from websockets.exceptions import ConnectionClosed

from ia_mcp.agent.protocol import (
    ChatPayload,
    PullPayload,
    WordEditPayload,
    WordReadPayload,
    build_chat_messages,
    valid_request_id,
    validate_payload,
)
from ia_mcp.integrations.local_services import LocalServiceError, LocalServices
from ia_mcp.integrations.word_service import WordService

LOGGER = logging.getLogger("ia_mcp.agent")
AUDIT = logging.getLogger("ia_mcp.agent.audit")

EventEmitter = Callable[[dict[str, Any]], Awaitable[None]]
EDIT_ACTIONS = frozenset({"word.append", "word.replace"})


class LocalAgent:
    def __init__(
        self,
        services: LocalServices,
        word: WordService,
        agent_token: str,
        relay_url: str,
        allow_edits: bool = False,
        agent_id: str = "Mi PC",
    ) -> None:
        self._services = services
        self._word = word
        self._agent_token = agent_token
        self._relay_url = relay_url
        self._allow_edits = allow_edits
        self._agent_id = agent_id[:80]

    async def dispatch(
        self,
        action: str,
        payload: dict[str, Any],
        emit: EventEmitter,
    ) -> Any:
        validated = validate_payload(action, payload)

        if action == "status":
            return await self._status()
        if action == "models.list":
            return await self._services.ollama_models()
        if action == "models.pull":
            pull = cast(PullPayload, validated)
            AUDIT.info("descarga de modelo iniciada: %s", pull.model)
            async for line in self._services.ollama_pull_stream(pull.model):
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("error"):
                    raise LocalServiceError(str(event["error"]))
                await emit(event)
            AUDIT.info("descarga de modelo finalizada: %s", pull.model)
            return {"model": pull.model}
        if action == "chat":
            chat = cast(ChatPayload, validated)
            messages = build_chat_messages(
                chat.instructions, chat.context, chat.messages
            )
            result = await self._services.ollama_chat(chat.model, messages)
            return {"model": chat.model, "message": result["message"]["content"]}
        if action == "word.status":
            return await self._word.status()
        if action == "word.documents":
            return await self._word.documents()
        if action == "word.read":
            read = cast(WordReadPayload, validated)
            return await self._word.read_document(read.document, read.max_chars)
        if action in EDIT_ACTIONS:
            if not self._allow_edits:
                raise LocalServiceError(
                    "Las ediciones remotas de Word estan desactivadas en el agente local."
                )
            edit = cast(WordEditPayload, validated)
            if not edit.confirm:
                raise LocalServiceError(
                    "La edicion requiere confirmacion explicita de la persona."
                )
            if action == "word.append":
                result = await self._word.append_text(edit.text, edit.document)
            else:
                result = await self._word.replace_text(edit.text, edit.document)
            AUDIT.info(
                "edicion de Word aplicada: %s documento=%s caracteres=%s",
                result.get("applied"),
                result.get("name"),
                result.get("characters"),
            )
            return result
        if action == "civil3d.alignments":
            return await self._services.list_alignments()
        raise ValueError("Accion no permitida.")

    async def handle_request(
        self,
        request: dict[str, Any],
        emit: EventEmitter,
    ) -> Any:
        action = request.get("action")
        if not isinstance(action, str):
            raise ValueError("La solicitud no incluye una accion valida.")
        payload = request.get("payload", {})
        if not isinstance(payload, dict):
            raise ValueError("El contenido de la solicitud no es valido.")
        return await self.dispatch(action, payload, emit)

    async def _status(self) -> dict[str, Any]:
        checks = (
            ("ollama", self._services.ollama_status),
            ("civil3d", self._services.civil3d_status),
            ("word", self._word.status),
        )

        async def run(name, check):
            try:
                return name, await check()
            except LocalServiceError as exc:
                return name, {"available": False, "error": str(exc)}

        return dict(await asyncio.gather(*(run(name, check) for name, check in checks)))

    async def run_forever(self) -> None:
        delay = 1
        while True:
            try:
                async with websockets.connect(
                    self._relay_url,
                    max_size=2_000_000,
                    ping_interval=20,
                    ping_timeout=20,
                ) as websocket:
                    await self._handshake(websocket)
                    LOGGER.info("Conectado al relay %s", self._relay_url)
                    AUDIT.info("agente conectado: %s", self._agent_id)
                    delay = 1
                    send_lock = asyncio.Lock()
                    tasks: set[asyncio.Task[Any]] = set()
                    try:
                        async for raw in websocket:
                            task = asyncio.create_task(
                                self._safe_handle(websocket, raw, send_lock)
                            )
                            tasks.add(task)
                            task.add_done_callback(tasks.discard)
                    finally:
                        for task in tasks:
                            task.cancel()
                        if tasks:
                            await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.warning(
                    "No se pudo mantener la conexion con el relay: %s", exc
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)

    async def _handshake(self, websocket: Any) -> None:
        await websocket.send(
            json.dumps(
                {
                    "type": "hello",
                    "role": "agent",
                    "token": self._agent_token,
                    "agent_id": self._agent_id,
                }
            )
        )
        raw = await asyncio.wait_for(websocket.recv(), timeout=15)
        try:
            response = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("El relay devolvio un saludo invalido.") from exc
        if not isinstance(response, dict) or response.get("ok") is not True:
            error = response.get("error") if isinstance(response, dict) else None
            raise RuntimeError(error or "El relay rechazo la conexion del agente.")

    async def _safe_handle(
        self,
        websocket: Any,
        raw: str | bytes,
        send_lock: asyncio.Lock,
    ) -> None:
        request_id: str | None = None
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get("type") != "request":
                return
            request_id = data.get("id")
            if not valid_request_id(request_id):
                return
            assert isinstance(request_id, str)

            async def emit(event: dict[str, Any]) -> None:
                await self._send(
                    websocket,
                    {"type": "event", "id": request_id, "event": event},
                    send_lock,
                )

            result = await self.handle_request(data, emit)
            await self._send(
                websocket,
                {"type": "result", "id": request_id, "ok": True, "result": result},
                send_lock,
            )
        except (LocalServiceError, ValueError) as exc:
            if request_id:
                await self._send_error(websocket, request_id, str(exc), send_lock)
        except asyncio.CancelledError:
            raise
        except ConnectionClosed:
            return
        except Exception:
            LOGGER.exception("Fallo inesperado al procesar una solicitud remota.")
            if request_id:
                await self._send_error(
                    websocket,
                    request_id,
                    "El agente no pudo completar la solicitud.",
                    send_lock,
                )

    async def _send_error(
        self,
        websocket: Any,
        request_id: str,
        message: str,
        send_lock: asyncio.Lock,
    ) -> None:
        try:
            await self._send(
                websocket,
                {"type": "error", "id": request_id, "error": message},
                send_lock,
            )
        except ConnectionClosed:
            pass

    @staticmethod
    async def _send(
        websocket: Any,
        payload: dict[str, Any],
        send_lock: asyncio.Lock,
    ) -> None:
        async with send_lock:
            await websocket.send(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            )


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    token = os.environ.get("IA_MCP_AGENT_TOKEN", "").strip()
    if len(token) < 16:
        raise SystemExit(
            "Configura IA_MCP_AGENT_TOKEN con un secreto de al menos 16 caracteres."
        )
    relay_url = os.environ.get(
        "IA_MCP_RELAY_URL", "ws://127.0.0.1:8765/ws/agent"
    ).strip()
    allow_edits = _env_flag("IA_MCP_AGENT_ALLOW_EDITS")
    agent_id = os.environ.get("IA_MCP_AGENT_NAME", "Mi PC")

    async def run() -> None:
        async with httpx.AsyncClient(follow_redirects=False) as client:
            agent = LocalAgent(
                services=LocalServices(client),
                word=WordService(),
                agent_token=token,
                relay_url=relay_url,
                allow_edits=allow_edits,
                agent_id=agent_id,
            )
            await agent.run_forever()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        LOGGER.info("Agente detenido.")


if __name__ == "__main__":
    main()
