from __future__ import annotations

from typing import Any

import pytest

from ia_mcp.integrations.local_services import LocalServiceError
from ia_mcp.integrations.word_service import WordService


class FakeWordBackend:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.read_calls: list[tuple[str | int | None, int]] = []
        self.edit_calls: list[tuple[str, str | int | None, str]] = []

    def __enter__(self) -> FakeWordBackend:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def documents(self) -> list[dict[str, Any]]:
        if self.error:
            raise self.error
        return [{"index": 1, "name": "informe.docx", "active": True}]

    def read(self, document: str | int | None, max_chars: int) -> dict[str, Any]:
        self.read_calls.append((document, max_chars))
        if self.error:
            raise self.error
        return {"name": "informe.docx", "text": "contenido"[:max_chars]}

    def append(self, document: str | int | None, text: str) -> dict[str, Any]:
        self.edit_calls.append(("append", document, text))
        if self.error:
            raise self.error
        return {"name": "informe.docx", "applied": "append"}

    def replace(self, document: str | int | None, text: str) -> dict[str, Any]:
        self.edit_calls.append(("replace", document, text))
        if self.error:
            raise self.error
        return {"name": "informe.docx", "applied": "replace"}


@pytest.mark.asyncio
async def test_word_documents_are_read_through_the_backend():
    backend = FakeWordBackend()
    service = WordService(backend_factory=lambda: backend)

    assert await service.documents() == [
        {"index": 1, "name": "informe.docx", "active": True}
    ]


@pytest.mark.asyncio
async def test_word_read_document_forwards_document_and_limit():
    backend = FakeWordBackend()
    service = WordService(backend_factory=lambda: backend)

    result = await service.read_document("informe.docx", max_chars=5)

    assert backend.read_calls == [("informe.docx", 5)]
    assert result == {"name": "informe.docx", "text": "conte"}


@pytest.mark.asyncio
async def test_word_append_and_replace_are_forwarded_to_the_backend():
    backend = FakeWordBackend()
    service = WordService(backend_factory=lambda: backend)

    appended = await service.append_text("Anexo", "informe.docx")
    replaced = await service.replace_text("Nuevo texto", "informe.docx")

    assert backend.edit_calls == [
        ("append", "informe.docx", "Anexo"),
        ("replace", "informe.docx", "Nuevo texto"),
    ]
    assert appended["applied"] == "append"
    assert replaced["applied"] == "replace"


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", "   ", "x" * 200001])
async def test_word_edits_reject_invalid_text(text):
    service = WordService(backend_factory=FakeWordBackend)

    with pytest.raises(LocalServiceError, match="texto"):
        await service.append_text(text)


@pytest.mark.asyncio
async def test_word_backend_errors_become_local_service_errors():
    backend = FakeWordBackend(error=RuntimeError("COM unavailable"))
    service = WordService(backend_factory=lambda: backend)

    with pytest.raises(LocalServiceError, match="COM unavailable"):
        await service.documents()


@pytest.mark.asyncio
@pytest.mark.parametrize("max_chars", [0, -1, 200001, True, "100"])
async def test_word_read_document_rejects_invalid_limits(max_chars):
    service = WordService(backend_factory=FakeWordBackend)

    with pytest.raises(LocalServiceError, match="max_chars"):
        await service.read_document(max_chars=max_chars)


@pytest.mark.asyncio
async def test_word_status_reports_installation_and_running_session():
    service = WordService(
        backend_factory=FakeWordBackend,
        installation_check=lambda: (True, "16.0.1"),
        running_probe=lambda: (True, 2, "16.0.1"),
    )

    status = await service.status()

    assert status["available"] is True
    assert status["installed"] is True
    assert status["running"] is True
    assert status["documents"] == 2
    assert status["version"] == "16.0.1"


@pytest.mark.asyncio
async def test_word_status_explains_missing_installation():
    service = WordService(
        backend_factory=FakeWordBackend,
        installation_check=lambda: (False, None),
        running_probe=lambda: (False, 0, None),
    )

    status = await service.status()

    assert status["available"] is False
    assert status["installed"] is False
    assert status["running"] is False
    assert "no esta instalado" in status["error"]
