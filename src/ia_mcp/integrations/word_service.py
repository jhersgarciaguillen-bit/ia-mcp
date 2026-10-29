from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Callable, Protocol

from ia_mcp.integrations.local_services import LocalServiceError

_WORD_PATHS = (
    Path(r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE"),
    Path(r"C:\Program Files (x86)\Microsoft Office\root\Office16\WINWORD.EXE"),
    Path(r"C:\Program Files\Microsoft Office\Office16\WINWORD.EXE"),
    Path(r"C:\Program Files (x86)\Microsoft Office\Office16\WINWORD.EXE"),
)


class WordBackend(Protocol):
    def __enter__(self) -> WordBackend: ...

    def __exit__(self, *exc_info: object) -> bool: ...

    def documents(self) -> list[dict[str, Any]]: ...

    def read(self, document: str | int | None, max_chars: int) -> dict[str, Any]: ...

    def append(self, document: str | int | None, text: str) -> dict[str, Any]: ...

    def replace(self, document: str | int | None, text: str) -> dict[str, Any]: ...


class ComWordBackend:
    """Access to the Word instance already running on this PC (read and confirmed edits)."""

    def __init__(self) -> None:
        self._app: Any | None = None
        self._pythoncom: Any | None = None

    def __enter__(self) -> ComWordBackend:
        try:
            import pythoncom
            import win32com.client
        except ImportError as exc:
            raise LocalServiceError(
                "La integracion con Word requiere pywin32 en Windows."
            ) from exc
        pythoncom.CoInitialize()
        self._pythoncom = pythoncom
        try:
            try:
                app = win32com.client.GetActiveObject("Word.Application")
            except Exception as exc:
                raise LocalServiceError(
                    "Microsoft Word no esta abierto. Abre Word y un documento para "
                    "consultarlos."
                ) from exc
        except Exception:
            pythoncom.CoUninitialize()
            self._pythoncom = None
            raise
        self._app = app
        return self

    def __exit__(self, *exc_info: object) -> bool:
        self._app = None
        if self._pythoncom is not None:
            self._pythoncom.CoUninitialize()
            self._pythoncom = None
        return False

    def documents(self) -> list[dict[str, Any]]:
        app = self._require_app()
        count = int(app.Documents.Count)
        active_name = _optional(lambda: str(app.ActiveDocument.Name))
        documents: list[dict[str, Any]] = []
        for index in range(1, count + 1):
            document = app.Documents(index)
            name = _optional(lambda: str(document.Name)) or f"documento {index}"
            documents.append(
                {
                    "index": index,
                    "name": name,
                    "active": name == active_name,
                    "saved": _optional(lambda: bool(document.Saved)),
                    "read_only": _optional(lambda: bool(document.ReadOnly)),
                }
            )
        return documents

    def read(self, document: str | int | None, max_chars: int) -> dict[str, Any]:
        selected = self._open_document(document)
        text = str(selected.Range().Text or "")
        total = len(text)
        return {
            "name": str(selected.Name),
            "characters": total,
            "truncated": total > max_chars,
            "text": text[:max_chars],
            "paragraphs": _optional(lambda: int(selected.Paragraphs.Count)),
            "tables": _optional(lambda: int(selected.Tables.Count)),
        }

    def append(self, document: str | int | None, text: str) -> dict[str, Any]:
        selected = self._open_document(document)
        selected.Content.InsertAfter(text)
        return {
            "name": str(selected.Name),
            "characters": len(str(selected.Range().Text or "")),
            "applied": "append",
        }

    def replace(self, document: str | int | None, text: str) -> dict[str, Any]:
        selected = self._open_document(document)
        selected.Content.Text = text
        return {
            "name": str(selected.Name),
            "characters": len(str(selected.Range().Text or "")),
            "applied": "replace",
        }

    def _open_document(self, document: str | int | None) -> Any:
        app = self._require_app()
        count = int(app.Documents.Count)
        if count == 0:
            raise LocalServiceError("No hay documentos abiertos en Word.")
        return self._select(app, count, document)

    def _select(self, app: Any, count: int, document: str | int | None) -> Any:
        key = "" if document is None else str(document).strip()
        if not key:
            try:
                return app.ActiveDocument
            except Exception:
                return app.Documents(1)
        if key.isdigit():
            index = int(key)
            if not 1 <= index <= count:
                raise LocalServiceError(
                    f"El indice {index} no corresponde a un documento abierto."
                )
            return app.Documents(index)
        target = key.casefold()
        for index in range(1, count + 1):
            candidate = app.Documents(index)
            name = _optional(lambda: str(candidate.Name))
            full_name = _optional(lambda: str(candidate.FullName))
            if target in {
                (name or "").casefold(),
                (full_name or "").casefold(),
            }:
                return candidate
        raise LocalServiceError(
            f"No hay un documento abierto llamado '{key}'. "
            "Usa word_list_documents para ver los documentos disponibles."
        )

    def _require_app(self) -> Any:
        if self._app is None:
            raise LocalServiceError("La sesion de Word no esta inicializada.")
        return self._app


class WordService:
    def __init__(
        self,
        backend_factory: Callable[[], WordBackend] | None = None,
        installation_check: Callable[[], tuple[bool, str | None]] | None = None,
        running_probe: Callable[[], tuple[bool, int, str | None]] | None = None,
    ) -> None:
        self._backend_factory = backend_factory or ComWordBackend
        self._installation_check = installation_check or _word_installation
        self._running_probe = running_probe or _probe_running_word

    async def status(self) -> dict[str, Any]:
        installed, version = await asyncio.to_thread(self._installation_check)
        running, documents, running_version = await asyncio.to_thread(
            self._running_probe
        )
        if running and running_version:
            version = running_version
        result: dict[str, Any] = {
            "available": installed,
            "installed": installed,
            "running": running,
            "documents": documents,
            "application": "Microsoft Word",
        }
        if version:
            result["version"] = version
        if not installed:
            result["error"] = "Microsoft Word no esta instalado en esta computadora."
        return result

    async def documents(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._invoke, "documents", None, 0)

    async def read_document(
        self, document: str | int | None = None, max_chars: int = 20000
    ) -> dict[str, Any]:
        if isinstance(max_chars, bool) or not isinstance(max_chars, int):
            raise LocalServiceError("max_chars debe ser un numero entero.")
        if not 1 <= max_chars <= 200000:
            raise LocalServiceError("max_chars debe estar entre 1 y 200000.")
        return await asyncio.to_thread(self._invoke, "read", document, max_chars)

    async def append_text(
        self, text: str, document: str | int | None = None
    ) -> dict[str, Any]:
        _validate_edit_text(text)
        return await asyncio.to_thread(self._invoke, "append", document, text)

    async def replace_text(
        self, text: str, document: str | int | None = None
    ) -> dict[str, Any]:
        _validate_edit_text(text)
        return await asyncio.to_thread(self._invoke, "replace", document, text)

    def _invoke(
        self, operation: str, document: str | int | None, value: Any
    ) -> Any:
        try:
            with self._backend_factory() as backend:
                if operation == "documents":
                    return backend.documents()
                if operation == "read":
                    return backend.read(document, value)
                if operation == "append":
                    return backend.append(document, value)
                return backend.replace(document, value)
        except LocalServiceError:
            raise
        except Exception as exc:
            raise LocalServiceError(
                f"Word no pudo completar la operacion: {exc}"
            ) from exc


def _validate_edit_text(text: str) -> None:
    if not isinstance(text, str):
        raise LocalServiceError("El texto para Word debe ser una cadena.")
    if not text.strip():
        raise LocalServiceError("El texto para Word no puede estar vacio.")
    if len(text) > 200000:
        raise LocalServiceError("El texto para Word no puede superar 200000 caracteres.")


def _optional(getter: Callable[[], Any]) -> Any | None:
    try:
        return getter()
    except Exception:
        return None


def _word_installation() -> tuple[bool, str | None]:
    if sys.platform != "win32":
        return False, None
    paths = list(_WORD_PATHS)
    version: str | None = None
    try:
        import winreg

        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                paths.extend(_app_path_candidates(winreg, hive, view))
                version = version or _office_version(winreg, hive, view)
    except OSError:
        pass
    installed = any(path.exists() for path in paths)
    return installed, version


def _app_path_candidates(winreg: Any, hive: int, view: int) -> list[Path]:
    try:
        with winreg.OpenKey(
            hive,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\WINWORD.EXE",
            0,
            winreg.KEY_READ | view,
        ) as key:
            value, _ = winreg.QueryValueEx(key, "")
    except OSError:
        return []
    return [Path(value)] if value else []


def _office_version(winreg: Any, hive: int, view: int) -> str | None:
    try:
        with winreg.OpenKey(
            hive,
            r"SOFTWARE\Microsoft\Office\ClickToRun\Configuration",
            0,
            winreg.KEY_READ | view,
        ) as key:
            value, _ = winreg.QueryValueEx(key, "VersionToReport")
    except OSError:
        return None
    return str(value) if value else None


def _probe_running_word() -> tuple[bool, int, str | None]:
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return False, 0, None
    pythoncom.CoInitialize()
    try:
        try:
            app = win32com.client.GetActiveObject("Word.Application")
        except Exception:
            return False, 0, None
        try:
            return True, int(app.Documents.Count), str(app.Version)
        except Exception:
            return True, 0, None
    finally:
        pythoncom.CoUninitialize()
