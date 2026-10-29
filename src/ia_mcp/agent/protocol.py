from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

ACTIONS = frozenset(
    {
        "status",
        "models.list",
        "models.pull",
        "chat",
        "word.status",
        "word.documents",
        "word.read",
        "word.append",
        "word.replace",
        "civil3d.alignments",
    }
)

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class AgentMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=50000)


class ChatPayload(BaseModel):
    model: str = Field(min_length=1, max_length=200)
    messages: list[AgentMessage] = Field(min_length=1, max_length=50)
    instructions: str = Field(default="", max_length=8000)
    context: str = Field(default="", max_length=200000)


class PullPayload(BaseModel):
    model: str = Field(
        min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._:/-]+$"
    )


class WordReadPayload(BaseModel):
    document: str | None = Field(default=None, max_length=260)
    max_chars: int = Field(default=20000, ge=1, le=200000)


class WordEditPayload(BaseModel):
    document: str | None = Field(default=None, max_length=260)
    text: str = Field(min_length=1, max_length=200000)
    confirm: bool = False


_PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "models.pull": PullPayload,
    "chat": ChatPayload,
    "word.read": WordReadPayload,
    "word.append": WordEditPayload,
    "word.replace": WordEditPayload,
}


def validate_payload(action: str, payload: dict[str, Any]) -> BaseModel | None:
    if action not in ACTIONS:
        raise ValueError("Accion no permitida.")
    if not isinstance(payload, dict):
        raise ValueError("El contenido de la solicitud no es valido.")
    model = _PAYLOAD_MODELS.get(action)
    if model is None:
        return None
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in first.get("loc", ()))
        message = first.get("msg", "datos invalidos")
        if location:
            raise ValueError(f"Datos invalidos en {location}: {message}.") from exc
        raise ValueError(f"Datos invalidos: {message}.") from exc


def build_chat_messages(
    instructions: str,
    context: str,
    messages: list[AgentMessage],
) -> list[dict[str, str]]:
    parts: list[str] = []
    if instructions.strip():
        parts.append(instructions.strip())
    if context.strip():
        parts.append(
            "Contexto cargado por la persona (Word o Civil 3D):\n\n"
            + context.strip()
        )
    result: list[dict[str, str]] = []
    if parts:
        result.append({"role": "system", "content": "\n\n".join(parts)})
    result.extend(message.model_dump() for message in messages)
    return result


def valid_request_id(value: Any) -> bool:
    return isinstance(value, str) and bool(REQUEST_ID_PATTERN.fullmatch(value))
