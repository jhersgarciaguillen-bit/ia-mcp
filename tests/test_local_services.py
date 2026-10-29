from __future__ import annotations

import json

import httpx
import pytest

from ia_mcp.integrations.local_services import LocalServiceError, LocalServices


@pytest.mark.asyncio
async def test_ollama_models_are_read_from_local_service():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://127.0.0.1:11434/api/tags"
        return httpx.Response(200, json={"models": [{"name": "local-model"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        services = LocalServices(client)
        assert await services.ollama_models() == [{"name": "local-model"}]


@pytest.mark.asyncio
async def test_ollama_pull_stream_forwards_progress_lines():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://127.0.0.1:11434/api/pull"
        assert json.loads(request.read()) == {"model": "new-model", "stream": True}
        return httpx.Response(
            200,
            content=(
                b'{"status":"pulling manifest"}\n'
                b'{"status":"success"}\n'
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        services = LocalServices(client)
        lines = [line async for line in services.ollama_pull_stream("new-model")]

    assert lines == ['{"status":"pulling manifest"}', '{"status":"success"}']


@pytest.mark.asyncio
async def test_ollama_chat_sends_messages_without_streaming():
    messages = [{"role": "user", "content": "Hola"}]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://127.0.0.1:11434/api/chat"
        assert json.loads(request.read()) == {
            "model": "local-model",
            "messages": messages,
            "stream": False,
        }
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "Hola, ¿qué necesitas?"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        services = LocalServices(client)
        result = await services.ollama_chat("local-model", messages)

    assert result["message"]["content"] == "Hola, ¿qué necesitas?"


@pytest.mark.asyncio
async def test_ollama_chat_rejects_invalid_payloads():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"role": "assistant"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        services = LocalServices(client)
        with pytest.raises(LocalServiceError, match="mensaje invalido"):
            await services.ollama_chat("local-model", [{"role": "user", "content": "Hi"}])


@pytest.mark.asyncio
async def test_civil3d_alignment_query_uses_allowlisted_read_tool_and_token():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://127.0.0.1:8756/rpc"
        assert request.headers["X-Ingenia-Token"] == "test-token"
        payload = request.read()
        assert b'"tool":"list_alignments"' in payload
        return httpx.Response(200, json={"ok": True, "data": {"alignments": []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        services = LocalServices(client, civil3d_token="test-token")
        result = await services.list_alignments()
        assert result["ok"] is True


@pytest.mark.asyncio
async def test_civil3d_failure_is_not_reported_as_success():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "bridge offline"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        services = LocalServices(client)
        with pytest.raises(LocalServiceError, match="bridge offline"):
            await services.list_alignments()
