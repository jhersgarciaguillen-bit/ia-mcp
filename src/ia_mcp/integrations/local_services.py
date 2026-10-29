from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

class LocalServiceError(RuntimeError):
    """A local model or engineering application could not complete a request."""


class LocalServices:
    def __init__(
        self,
        client: httpx.AsyncClient,
        ollama_base_url: str | None = None,
        civil3d_base_url: str | None = None,
        civil3d_token: str | None = None,
    ) -> None:
        self._client = client
        self._ollama_base_url = (
            ollama_base_url or os.getenv(
                "OLLAMA_BASE_URL", "http://127.0.0.1:11434"
            )
        ).rstrip("/")
        self._civil3d_base_url = (
            civil3d_base_url or os.getenv(
                "CIVIL3D_BASE_URL", "http://127.0.0.1:8756"
            )
        ).rstrip("/")
        self._civil3d_token = (
            civil3d_token if civil3d_token is not None
            else os.getenv("CIVIL3D_TOKEN", "")
        )

    async def ollama_status(self) -> dict[str, Any]:
        try:
            response = await self._client.get(
                f"{self._ollama_base_url}/api/tags", timeout=3
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LocalServiceError(f"Ollama no esta disponible: {exc}") from exc
        return {"available": True}

    async def ollama_models(self) -> list[dict[str, Any]]:
        try:
            response = await self._client.get(
                f"{self._ollama_base_url}/api/tags", timeout=15
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LocalServiceError(
                f"No se pudieron consultar los modelos de Ollama: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise LocalServiceError("Ollama devolvio una respuesta invalida.")
        models = payload.get("models")
        if not isinstance(models, list):
            raise LocalServiceError("Ollama devolvio una lista de modelos invalida.")
        return models

    async def ollama_pull_stream(self, model: str) -> AsyncIterator[str]:
        try:
            async with self._client.stream(
                "POST",
                f"{self._ollama_base_url}/api/pull",
                json={"model": model, "stream": True},
                timeout=httpx.Timeout(30, read=1800),
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.strip():
                        yield line
        except httpx.HTTPError as exc:
            raise LocalServiceError(
                f"Ollama no pudo descargar el modelo: {exc}"
            ) from exc

    async def ollama_chat(
        self, model: str, messages: list[dict[str, Any]]
    ) -> dict[str, Any]:
        try:
            response = await self._client.post(
                f"{self._ollama_base_url}/api/chat",
                json={"model": model, "messages": messages, "stream": False},
                timeout=300,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LocalServiceError(
                f"Ollama no pudo completar la conversacion: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise LocalServiceError("Ollama devolvio una respuesta invalida.")
        message = payload.get("message")
        if not isinstance(message, dict) or not isinstance(
            message.get("content"), str
        ):
            raise LocalServiceError("Ollama devolvio un mensaje invalido.")
        return payload

    def _civil3d_headers(self) -> dict[str, str]:
        if not self._civil3d_token:
            return {}
        return {"X-Ingenia-Token": self._civil3d_token}

    async def civil3d_status(self) -> dict[str, Any]:
        try:
            response = await self._client.get(
                f"{self._civil3d_base_url}/status",
                headers=self._civil3d_headers(),
                timeout=5,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LocalServiceError(
                f"El puente local de Civil 3D no esta disponible: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise LocalServiceError("Civil 3D devolvio un estado invalido.")
        return payload

    async def list_alignments(self) -> dict[str, Any]:
        try:
            response = await self._client.post(
                f"{self._civil3d_base_url}/rpc",
                headers=self._civil3d_headers(),
                json={
                    "tool": "list_alignments",
                    "args": {},
                    "request_id": uuid.uuid4().hex,
                },
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LocalServiceError(
                f"No se pudieron consultar los alineamientos de Civil 3D: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise LocalServiceError("Civil 3D devolvio una respuesta invalida.")
        if payload.get("ok") is not True:
            detail = payload.get("error", "El puente rechazo la consulta.")
            raise LocalServiceError(str(detail))
        return payload
