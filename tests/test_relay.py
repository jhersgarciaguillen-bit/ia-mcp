from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from ia_mcp.relay.app import create_relay_app
from ia_mcp.relay.hub import RelayHub

AGENT_TOKEN = "agent-token-0123456789"
CLIENT_TOKEN = "client-token-0123456789"


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed: list[tuple[int, str]] = []

    async def send_text(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed.append((code, reason))


def request(request_id: str, action: str = "models.list", payload=None) -> str:
    return json.dumps(
        {
            "type": "request",
            "id": request_id,
            "action": action,
            "payload": payload or {},
        }
    )


def test_hub_authorizes_roles_with_constant_time_tokens():
    hub = RelayHub(AGENT_TOKEN, CLIENT_TOKEN)

    assert hub.authorize("agent", AGENT_TOKEN)
    assert hub.authorize("client", CLIENT_TOKEN)
    assert not hub.authorize("client", AGENT_TOKEN)
    assert not hub.authorize("agent", "")
    assert not hub.authorize("agent", None)


@pytest.mark.asyncio
async def test_hub_routes_requests_events_and_results_to_the_requesting_client():
    hub = RelayHub(AGENT_TOKEN, CLIENT_TOKEN)
    agent = FakeWebSocket()
    client = FakeWebSocket()
    await hub.agent_connected(agent, "pc")
    await hub.client_connected(client)

    await hub.client_message(client, request("req-1"))
    assert agent.sent[0]["action"] == "models.list"

    await hub.agent_message(
        agent, json.dumps({"type": "event", "id": "req-1", "event": {"progress": 1}})
    )
    await hub.agent_message(
        agent,
        json.dumps({"type": "result", "id": "req-1", "ok": True, "result": ["a"]}),
    )
    await hub.agent_message(
        agent,
        json.dumps({"type": "result", "id": "req-1", "ok": True, "result": ["late"]}),
    )

    assert [message["type"] for message in client.sent] == ["event", "result"]
    assert client.sent[-1]["result"] == ["a"]


@pytest.mark.asyncio
async def test_hub_rejects_unknown_actions_and_missing_agent():
    hub = RelayHub(AGENT_TOKEN, CLIENT_TOKEN)
    client = FakeWebSocket()
    await hub.client_connected(client)

    await hub.client_message(client, request("req-1", action="shell.exec"))
    assert "no permitida" in client.sent[-1]["error"]

    await hub.client_message(client, request("req-2"))
    assert "no esta conectado" in client.sent[-1]["error"]


@pytest.mark.asyncio
async def test_hub_fails_pending_requests_when_agent_disconnects():
    hub = RelayHub(AGENT_TOKEN, CLIENT_TOKEN)
    agent = FakeWebSocket()
    client = FakeWebSocket()
    await hub.agent_connected(agent, "pc")
    await hub.client_connected(client)
    await hub.client_message(client, request("req-1"))

    await hub.agent_disconnected(agent)

    assert client.sent[0] == {
        "type": "error",
        "id": "req-1",
        "error": "El agente local se desconecto.",
    }
    assert client.sent[1] == {"type": "agent_offline"}


@pytest.mark.asyncio
async def test_hub_replaces_an_old_agent_connection():
    hub = RelayHub(AGENT_TOKEN, CLIENT_TOKEN)
    first = FakeWebSocket()
    second = FakeWebSocket()

    await hub.agent_connected(first, "pc")
    await hub.agent_connected(second, "pc-2")

    assert first.closed and first.closed[0][0] == 4001
    assert hub.agent_online is True


def test_relay_serves_remote_ui_and_health():
    app = create_relay_app(AGENT_TOKEN, CLIENT_TOKEN)
    with TestClient(app) as client:
        assert client.get("/api/health").json()["relay"] == "IA_MCP"
        redirect = client.get("/", follow_redirects=False)
        assert redirect.status_code == 307
        assert redirect.headers["location"] == "/remote"
        page = client.get("/remote")
        assert page.status_code == 200
        assert "IA_MCP" in page.text


def test_relay_routes_between_real_websockets():
    app = create_relay_app(AGENT_TOKEN, CLIENT_TOKEN)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/agent") as agent:
            agent.send_json({"type": "hello", "role": "agent", "token": AGENT_TOKEN})
            assert agent.receive_json()["ok"] is True
            with client.websocket_connect("/ws/client") as browser:
                browser.send_json(
                    {"type": "hello", "role": "client", "token": CLIENT_TOKEN}
                )
                assert browser.receive_json()["agent_online"] is True
                browser.send_json(
                    {
                        "type": "request",
                        "id": "req-1",
                        "action": "models.list",
                        "payload": {},
                    }
                )
                assert agent.receive_json()["action"] == "models.list"
                agent.send_json(
                    {"type": "event", "id": "req-1", "event": {"status": "pull"}}
                )
                assert browser.receive_json()["event"] == {"status": "pull"}
                agent.send_json(
                    {
                        "type": "result",
                        "id": "req-1",
                        "ok": True,
                        "result": ["modelo"],
                    }
                )
                assert browser.receive_json()["result"] == ["modelo"]


def test_relay_websocket_authentication():
    app = create_relay_app(AGENT_TOKEN, CLIENT_TOKEN)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/client") as websocket:
            websocket.send_json(
                {"type": "hello", "role": "client", "token": "wrong-token-000000"}
            )
            assert websocket.receive_json() == {
                "type": "hello",
                "ok": False,
                "error": "No autorizado.",
            }

        with client.websocket_connect("/ws/agent") as websocket:
            websocket.send_json(
                {"type": "hello", "role": "agent", "token": AGENT_TOKEN}
            )
            hello = websocket.receive_json()
            assert hello["ok"] is True
            assert hello["role"] == "agent"
