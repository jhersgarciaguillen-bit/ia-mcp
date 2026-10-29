from __future__ import annotations

from typing import Any

import pytest

from ia_mcp.agent.local_agent import LocalAgent
from ia_mcp.agent.protocol import valid_request_id
from ia_mcp.integrations.local_services import LocalServiceError


class FakeServices:
    def __init__(self) -> None:
        self.chat_calls: list[tuple[str, list[dict]]] = []
        self.pull_calls: list[str] = []

    async def ollama_status(self):
        return {"available": True}

    async def civil3d_status(self):
        raise LocalServiceError("sin puente")

    async def ollama_models(self):
        return [{"name": "modelo-local"}]

    async def ollama_pull_stream(self, model: str):
        self.pull_calls.append(model)
        yield '{"status":"pulling manifest"}'
        yield '{"status":"success"}'

    async def ollama_chat(self, model: str, messages: list[dict]):
        self.chat_calls.append((model, messages))
        return {"message": {"role": "assistant", "content": "respuesta"}}

    async def list_alignments(self):
        return {"ok": True, "data": {"alignments": []}}


class FakeWord:
    def __init__(self) -> None:
        self.edits: list[tuple[str, str | None, str]] = []

    async def status(self):
        return {"installed": True, "running": False, "available": True}

    async def documents(self):
        return [{"index": 1, "name": "informe.docx"}]

    async def read_document(self, document=None, max_chars=20000):
        return {"name": "informe.docx", "text": "texto", "characters": 5}

    async def append_text(self, text, document=None):
        self.edits.append(("append", document, text))
        return {"name": "informe.docx", "characters": len(text), "applied": "append"}

    async def replace_text(self, text, document=None):
        self.edits.append(("replace", document, text))
        return {"name": "informe.docx", "characters": len(text), "applied": "replace"}


def make_agent(allow_edits: bool = False, services=None, word=None) -> LocalAgent:
    return LocalAgent(
        services=services or FakeServices(),
        word=word or FakeWord(),
        agent_token="a" * 32,
        relay_url="ws://127.0.0.1:8765/ws/agent",
        allow_edits=allow_edits,
        agent_id="test",
    )


async def _noop(_event: dict[str, Any]) -> None:
    return None


@pytest.mark.asyncio
async def test_status_collects_services_and_isolates_failures():
    result = await make_agent().dispatch("status", {}, _noop)

    assert result["ollama"] == {"available": True}
    assert result["civil3d"] == {"available": False, "error": "sin puente"}
    assert result["word"]["installed"] is True


@pytest.mark.asyncio
async def test_chat_builds_system_context_and_returns_message():
    services = FakeServices()
    agent = make_agent(services=services)
    payload = {
        "model": "modelo-local",
        "messages": [{"role": "user", "content": "Resume"}],
        "instructions": "Responde breve.",
        "context": "Texto cargado.",
    }

    result = await agent.dispatch("chat", payload, _noop)

    assert result == {"model": "modelo-local", "message": "respuesta"}
    model, messages = services.chat_calls[0]
    assert model == "modelo-local"
    assert messages[0]["role"] == "system"
    assert "Responde breve." in messages[0]["content"]
    assert "Texto cargado." in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": "Resume"}


@pytest.mark.asyncio
async def test_model_pull_emits_events_and_result():
    services = FakeServices()
    agent = make_agent(services=services)
    events: list[dict[str, Any]] = []

    async def emit(event):
        events.append(event)

    result = await agent.dispatch("models.pull", {"model": "nuevo:1b"}, emit)

    assert result == {"model": "nuevo:1b"}
    assert services.pull_calls == ["nuevo:1b"]
    assert [event["status"] for event in events] == ["pulling manifest", "success"]


@pytest.mark.asyncio
async def test_word_edits_are_disabled_by_default():
    word = FakeWord()
    agent = make_agent(word=word)

    with pytest.raises(LocalServiceError, match="desactivadas"):
        await agent.dispatch(
            "word.append",
            {"text": "Anexo", "confirm": True},
            _noop,
        )
    assert word.edits == []


@pytest.mark.asyncio
async def test_word_edits_require_confirmation_even_when_enabled():
    word = FakeWord()
    agent = make_agent(allow_edits=True, word=word)

    with pytest.raises(LocalServiceError, match="confirmacion"):
        await agent.dispatch(
            "word.append",
            {"text": "Anexo", "confirm": False},
            _noop,
        )
    assert word.edits == []


@pytest.mark.asyncio
async def test_word_edits_are_applied_when_enabled_and_confirmed():
    word = FakeWord()
    agent = make_agent(allow_edits=True, word=word)

    result = await agent.dispatch(
        "word.append",
        {"document": "1", "text": "Anexo", "confirm": True},
        _noop,
    )

    assert result["applied"] == "append"
    assert word.edits == [("append", "1", "Anexo")]


@pytest.mark.asyncio
async def test_unknown_action_and_invalid_payload_are_rejected():
    agent = make_agent()

    with pytest.raises(ValueError, match="no permitida"):
        await agent.dispatch("shell.exec", {}, _noop)
    with pytest.raises(ValueError, match="invalid"):
        await agent.dispatch("chat", {"model": "x"}, _noop)


@pytest.mark.asyncio
async def test_handle_request_validates_shape():
    agent = make_agent()

    with pytest.raises(ValueError, match="accion"):
        await agent.handle_request({"payload": {}}, _noop)
    with pytest.raises(ValueError, match="contenido"):
        await agent.handle_request({"action": "status", "payload": []}, _noop)


class FakeAgentWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, raw: str) -> None:
        import json

        self.sent.append(json.loads(raw))


@pytest.mark.asyncio
async def test_agent_websocket_handler_sends_events_and_result():
    import asyncio
    import json

    agent = make_agent()
    websocket = FakeAgentWebSocket()
    request = {
        "type": "request",
        "id": "req-1",
        "action": "models.pull",
        "payload": {"model": "nuevo:1b"},
    }

    await agent._safe_handle(websocket, json.dumps(request), asyncio.Lock())

    assert [message["type"] for message in websocket.sent] == ["event", "event", "result"]
    assert websocket.sent[-1]["result"] == {"model": "nuevo:1b"}


@pytest.mark.asyncio
async def test_agent_websocket_handler_sends_errors_for_rejected_actions():
    import asyncio
    import json

    agent = make_agent()
    websocket = FakeAgentWebSocket()

    await agent._safe_handle(
        websocket,
        json.dumps(
            {
                "type": "request",
                "id": "req-2",
                "action": "shell.exec",
                "payload": {},
            }
        ),
        asyncio.Lock(),
    )

    assert websocket.sent == [
        {"type": "error", "id": "req-2", "error": "Accion no permitida."}
    ]


def test_request_ids_have_a_strict_pattern():
    assert valid_request_id("abc-123_XYZ")
    assert not valid_request_id("")
    assert not valid_request_id("x" * 65)
    assert not valid_request_id("../etc/passwd")
