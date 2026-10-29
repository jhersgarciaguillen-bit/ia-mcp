from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ia_mcp.integrations.word_service import WordService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform != "win32", reason="La automatizacion de Word solo existe en Windows."
    ),
]

pytest.importorskip("win32com.client")

_CHILD_SCRIPT = r"""
import asyncio
import json
import os
import tempfile
from pathlib import Path


async def main():
    import win32com.client
    from ia_mcp.integrations.word_service import WordService

    service = WordService()
    if (await service.status())["running"]:
        print("WORD_SKIP_RUNNING", flush=True)
        return

    app = win32com.client.DispatchEx("Word.Application")
    document = None
    try:
        app.Visible = False
        app.DisplayAlerts = 0
        document = app.Documents.Add()
        document.Content.Text = "Hola desde IA_MCP.\rSegunda linea."
        document.SaveAs2(str(Path(tempfile.mkdtemp()) / "ia_mcp_word_test.docx"))

        documents = await service.documents()
        truncated = await service.read_document("ia_mcp_word_test.docx", max_chars=11)
        full = await service.read_document(None, max_chars=200000)
        appended = await service.append_text(" Anexo confirmado.", None)
        after_append = await service.read_document(None, max_chars=200000)
        replaced = await service.replace_text("Texto reemplazado.", None)
        after_replace = await service.read_document(None, max_chars=200000)
        status = await service.status()
        print(
            "WORD_RESULT:"
            + json.dumps(
                {
                    "documents": documents,
                    "truncated": truncated,
                    "full_text": full["text"],
                    "full_truncated": full["truncated"],
                    "appended": appended,
                    "after_append_text": after_append["text"],
                    "replaced": replaced,
                    "after_replace_text": after_replace["text"],
                    "status": status,
                }
            ),
            flush=True,
        )
    finally:
        if document is not None:
            document.Close(SaveChanges=0)
        app.Quit()


asyncio.run(main())
os._exit(0)
"""


@pytest.mark.asyncio
async def test_word_status_detects_a_real_installation():
    status = await WordService().status()

    if not status["installed"]:
        pytest.skip("Microsoft Word no esta instalado en esta computadora.")
    assert status["available"] is True


def test_word_reads_a_real_open_document(tmp_path):
    if not WordService()._installation_check()[0]:
        pytest.skip("Microsoft Word no esta instalado en esta computadora.")

    env = os.environ.copy()
    source = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = source + os.pathsep + env.get("PYTHONPATH", "")

    completed = subprocess.run(
        [sys.executable, "-c", _CHILD_SCRIPT, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
        cwd=str(tmp_path),
    )

    output = completed.stdout + completed.stderr
    if "WORD_SKIP_RUNNING" in completed.stdout:
        pytest.skip("Hay otra sesion de Word abierta; no se tocan documentos del usuario.")
    assert completed.returncode == 0, output

    lines = [
        line for line in completed.stdout.splitlines() if line.startswith("WORD_RESULT:")
    ]
    assert lines, output
    result = json.loads(lines[-1].split(":", 1)[1])

    assert "ia_mcp_word_test.docx" in {
        document["name"] for document in result["documents"]
    }
    assert result["truncated"]["name"] == "ia_mcp_word_test.docx"
    assert result["truncated"]["truncated"] is True
    assert result["truncated"]["characters"] > 11
    assert result["truncated"]["text"] == "Hola desde "
    assert "Hola desde IA_MCP." in result["full_text"]
    assert "Segunda linea." in result["full_text"]
    assert result["full_truncated"] is False
    assert result["appended"]["applied"] == "append"
    assert "Anexo confirmado." in result["after_append_text"]
    assert result["replaced"]["applied"] == "replace"
    assert result["after_replace_text"].strip() == "Texto reemplazado."
    assert result["status"]["running"] is True
    assert result["status"]["documents"] == 1
