from __future__ import annotations

from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from ia_mcp.integrations.local_services import LocalServices
from ia_mcp.integrations.word_service import WordService

mcp = FastMCP(
    "IA_MCP Local",
    instructions=(
        "Consulta Ollama, Microsoft Word y aplicaciones locales mediante herramientas de "
        "solo lectura. No afirmes que una aplicacion esta disponible si la herramienta "
        "devuelve un error."
    ),
)
_client = httpx.AsyncClient(follow_redirects=False)
_services = LocalServices(
    client=_client,
)
_word = WordService()


@mcp.tool()
async def ollama_status() -> dict:
    """Comprueba si Ollama responde en esta computadora."""
    return await _services.ollama_status()


@mcp.tool()
async def ollama_list_models() -> list[dict[str, Any]]:
    """Lista los modelos descargados en Ollama en esta computadora."""
    return await _services.ollama_models()


@mcp.tool()
async def civil3d_status() -> dict:
    """Consulta el estado del puente local de Civil 3D."""
    return await _services.civil3d_status()


@mcp.tool()
async def civil3d_list_alignments() -> dict:
    """Lista alineamientos del dibujo activo sin modificarlo."""
    return await _services.list_alignments()


@mcp.tool()
async def word_status() -> dict:
    """Comprueba si Microsoft Word esta instalado y si tiene documentos abiertos."""
    return await _word.status()


@mcp.tool()
async def word_list_documents() -> list[dict]:
    """Lista los documentos abiertos en Microsoft Word sin modificarlos."""
    return await _word.documents()


@mcp.tool()
async def word_read_document(
    document: str | None = None, max_chars: int = 20000
) -> dict:
    """Lee el texto de un documento abierto en Word sin modificarlo.

    `document` puede ser el nombre/indice del documento abierto o vacio para el activo.
    """
    return await _word.read_document(document=document, max_chars=max_chars)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
