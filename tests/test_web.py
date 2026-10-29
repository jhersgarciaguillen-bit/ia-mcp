from fastapi.testclient import TestClient

from ia_mcp.web.app import app


class FakeLocalServices:
    def __init__(self) -> None:
        self.chat_calls: list[tuple[str, list[dict]]] = []
        self.pull_calls: list[str] = []

    async def ollama_status(self):
        return {"available": True}

    async def civil3d_status(self):
        return {"available": False, "error": "bridge offline"}

    async def ollama_models(self):
        return [{"name": "local-model"}, {"name": "other-model"}]

    async def ollama_chat(self, model, messages):
        self.chat_calls.append((model, messages))
        return {"message": {"role": "assistant", "content": "respuesta local"}}

    async def ollama_pull_stream(self, model):
        self.pull_calls.append(model)
        yield '{"status":"pulling manifest"}'
        yield '{"status":"success"}'

    async def list_alignments(self):
        return {"ok": True, "data": {"alignments": []}}


class FakeWordService:
    def __init__(self) -> None:
        self.edits: list[tuple[str, str | None, str]] = []

    async def status(self):
        return {"available": True, "installed": True, "running": False, "documents": 0}

    async def documents(self):
        return [{"index": 1, "name": "informe.docx", "active": True}]

    async def read_document(self, document=None, max_chars=20000):
        return {
            "name": "informe.docx",
            "characters": 12,
            "truncated": False,
            "text": "texto de word",
        }

    async def append_text(self, text, document=None):
        self.edits.append(("append", document, text))
        return {"name": "informe.docx", "characters": len(text), "applied": "append"}

    async def replace_text(self, text, document=None):
        self.edits.append(("replace", document, text))
        return {"name": "informe.docx", "characters": len(text), "applied": "replace"}


def test_health_and_home_page():
    with TestClient(app) as client:
        health = client.get("/api/health")
        page = client.get("/")

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "app": "IA_MCP"}
    assert page.status_code == 200
    assert "IA_MCP" in page.text
    assert "Conversación" in page.text


def test_local_status_rejects_non_loopback_clients():
    with TestClient(app, client=("198.51.100.24", 50000)) as client:
        response = client.get("/api/local/status")

    assert response.status_code == 403


def test_local_status_reports_ollama_civil3d_and_word():
    with TestClient(app) as client:
        app.state.local_services = FakeLocalServices()
        app.state.word_service = FakeWordService()
        response = client.get("/api/local/status")

    assert response.status_code == 200
    assert response.json() == {
        "ollama": {"available": True},
        "civil3d": {"available": False, "error": "bridge offline"},
        "word": {
            "available": True,
            "installed": True,
            "running": False,
            "documents": 0,
        },
    }


def test_models_endpoint_lists_ollama_models():
    with TestClient(app) as client:
        app.state.local_services = FakeLocalServices()
        response = client.get("/api/models")

    assert response.status_code == 200
    assert response.json() == [{"name": "local-model"}, {"name": "other-model"}]


def test_chat_adds_instructions_and_word_context_as_system_message():
    with TestClient(app) as client:
        services = FakeLocalServices()
        app.state.local_services = services
        response = client.post(
            "/api/chat",
            json={
                "model": "local-model",
                "messages": [{"role": "user", "content": "Resume el documento"}],
                "instructions": "Responde en español.",
                "context": "Contenido del informe.",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"model": "local-model", "message": "respuesta local"}
    assert services.chat_calls[0][0] == "local-model"
    messages = services.chat_calls[0][1]
    assert messages[0]["role"] == "system"
    assert "Responde en español." in messages[0]["content"]
    assert "Contenido del informe." in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": "Resume el documento"}


def test_chat_rejects_non_loopback_clients():
    body = {
        "model": "local-model",
        "messages": [{"role": "user", "content": "Hola"}],
    }
    with TestClient(app, client=("198.51.100.24", 50000)) as client:
        response = client.post("/api/chat", json=body)

    assert response.status_code == 403


def test_chat_validates_empty_messages():
    with TestClient(app) as client:
        response = client.post("/api/chat", json={"model": "local-model", "messages": []})

    assert response.status_code == 422


def test_pull_model_streams_progress_from_ollama():
    with TestClient(app) as client:
        services = FakeLocalServices()
        app.state.local_services = services
        with client.stream(
            "POST", "/api/models/pull", json={"model": "llama3.2:3b"}
        ) as response:
            body = "".join(response.iter_text())

    assert response.status_code == 200
    assert services.pull_calls == ["llama3.2:3b"]
    assert '{"status":"pulling manifest"}' in body
    assert '{"status":"success"}' in body


def test_pull_model_validates_model_name():
    with TestClient(app) as client:
        response = client.post("/api/models/pull", json={"model": "bad model!"})

    assert response.status_code == 422


def test_word_edits_require_explicit_confirmation():
    with TestClient(app) as client:
        word = FakeWordService()
        app.state.word_service = word
        response = client.post(
            "/api/word/append", json={"text": "Anexo", "confirm": False}
        )

    assert response.status_code == 400
    assert word.edits == []


def test_word_edits_are_applied_when_confirmed():
    with TestClient(app) as client:
        word = FakeWordService()
        app.state.word_service = word
        response = client.post(
            "/api/word/replace",
            json={"document": "1", "text": "Texto nuevo", "confirm": True},
        )

    assert response.status_code == 200
    assert response.json()["applied"] == "replace"
    assert word.edits == [("replace", "1", "Texto nuevo")]


def test_civil3d_alignments_endpoint():
    with TestClient(app) as client:
        app.state.local_services = FakeLocalServices()
        response = client.get("/api/civil3d/alignments")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "data": {"alignments": []}}


def test_word_endpoints_list_and_read_open_documents():
    with TestClient(app) as client:
        app.state.word_service = FakeWordService()
        documents = client.get("/api/word/documents")
        read = client.post("/api/word/read", json={"document": "1", "max_chars": 100})

    assert documents.status_code == 200
    assert documents.json() == [{"index": 1, "name": "informe.docx", "active": True}]
    assert read.status_code == 200
    assert read.json()["text"] == "texto de word"
