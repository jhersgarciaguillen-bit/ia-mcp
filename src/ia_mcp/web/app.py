from __future__ import annotations

import asyncio
import ipaddress
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ia_mcp.integrations.local_services import LocalServiceError, LocalServices
from ia_mcp.integrations.word_service import WordService

STATIC_DIR = Path(__file__).with_name("static")
LOOPBACK_ERROR = "Esta funcion solo esta disponible desde esta computadora."


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=50000)


class ChatRequest(BaseModel):
    model: str = Field(min_length=1, max_length=200)
    messages: list[ChatMessage] = Field(min_length=1, max_length=50)
    instructions: str = Field(default="", max_length=8000)
    context: str = Field(default="", max_length=200000)


class WordReadRequest(BaseModel):
    document: str | None = Field(default=None, max_length=260)
    max_chars: int = Field(default=20000, ge=1, le=200000)


class WordEditRequest(BaseModel):
    document: str | None = Field(default=None, max_length=260)
    text: str = Field(min_length=1, max_length=200000)
    confirm: bool = False


class PullRequest(BaseModel):
    model: str = Field(
        min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._:/-]+$"
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient(follow_redirects=False) as client:
        app.state.local_services = LocalServices(client)
        app.state.word_service = WordService()
        yield


app = FastAPI(title="IA_MCP", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "app": "IA_MCP"}


@app.get("/api/models")
async def models(request: Request) -> list[dict[str, Any]]:
    _require_loopback(request)
    services: LocalServices = app.state.local_services
    try:
        return await services.ollama_models()
    except LocalServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/models/pull")
async def pull_model(request: Request, payload: PullRequest) -> StreamingResponse:
    _require_loopback(request)
    services: LocalServices = app.state.local_services

    async def lines():
        try:
            async for line in services.ollama_pull_stream(payload.model):
                yield line + "\n"
        except LocalServiceError as exc:
            yield json.dumps({"error": str(exc)}) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson")


@app.post("/api/chat")
async def chat(request: Request, payload: ChatRequest) -> dict[str, str]:
    _require_loopback(request)
    services: LocalServices = app.state.local_services
    parts: list[str] = []
    if payload.instructions.strip():
        parts.append(payload.instructions.strip())
    if payload.context.strip():
        parts.append(
            "Contexto cargado por la persona (Word o Civil 3D):\n\n"
            + payload.context.strip()
        )
    messages: list[dict[str, Any]] = []
    if parts:
        messages.append({"role": "system", "content": "\n\n".join(parts)})
    messages.extend(message.model_dump() for message in payload.messages)
    try:
        result = await services.ollama_chat(payload.model, messages)
    except LocalServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"model": payload.model, "message": result["message"]["content"]}


@app.get("/api/word/documents")
async def word_documents(request: Request) -> list[dict[str, Any]]:
    _require_loopback(request)
    word: WordService = app.state.word_service
    try:
        return await word.documents()
    except LocalServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/word/read")
async def word_read(request: Request, payload: WordReadRequest) -> dict[str, Any]:
    _require_loopback(request)
    word: WordService = app.state.word_service
    try:
        return await word.read_document(payload.document, payload.max_chars)
    except LocalServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/word/append")
async def word_append(request: Request, payload: WordEditRequest) -> dict[str, Any]:
    _require_loopback(request)
    if not payload.confirm:
        raise HTTPException(
            status_code=400,
            detail="La edicion requiere confirmacion explicita de la persona.",
        )
    word: WordService = app.state.word_service
    try:
        return await word.append_text(payload.text, payload.document)
    except LocalServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/word/replace")
async def word_replace(request: Request, payload: WordEditRequest) -> dict[str, Any]:
    _require_loopback(request)
    if not payload.confirm:
        raise HTTPException(
            status_code=400,
            detail="La edicion requiere confirmacion explicita de la persona.",
        )
    word: WordService = app.state.word_service
    try:
        return await word.replace_text(payload.text, payload.document)
    except LocalServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/civil3d/alignments")
async def civil3d_alignments(request: Request) -> dict[str, Any]:
    _require_loopback(request)
    services: LocalServices = app.state.local_services
    try:
        return await services.list_alignments()
    except LocalServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/local/status")
async def local_status(request: Request) -> dict[str, dict]:
    _require_loopback(request)
    services: LocalServices = app.state.local_services
    word: WordService = app.state.word_service
    checks: tuple[tuple[str, Callable[[], Awaitable[dict]]], ...] = (
        ("ollama", services.ollama_status),
        ("civil3d", services.civil3d_status),
        ("word", word.status),
    )

    async def run(name: str, check: Callable[[], Awaitable[dict]]) -> tuple[str, dict]:
        try:
            return name, await check()
        except LocalServiceError as exc:
            return name, {"available": False, "error": str(exc)}

    return dict(await asyncio.gather(*(run(name, check) for name, check in checks)))


def _require_loopback(request: Request) -> None:
    client = request.client
    if client is None or not _is_loopback(client.host):
        raise HTTPException(status_code=403, detail=LOOPBACK_ERROR)


def _is_loopback(host: str) -> bool:
    if host in {"localhost", "testclient", "testserver"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
