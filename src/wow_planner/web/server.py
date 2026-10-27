"""Servidor HTTP local (somente biblioteca padrão) que serve a interface e a API JSON.

Escuta apenas em 127.0.0.1: nada fica exposto na rede.
"""

from __future__ import annotations

import json
import tempfile
import threading
import webbrowser
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from wow_planner.config import Settings
from wow_planner.repositories import connect, migrate
from wow_planner.services import CollectionService
from wow_planner.web.api import Api, ApiError

STATIC_DIR = Path(__file__).parent / "static"


def _make_handler(settings: Settings) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "WowPlanner/0.2"

        def log_message(self, fmt: str, *args: Any) -> None:  # silencioso
            pass

        # ---------------------------------------------------------- helpers
        def _api(self) -> tuple[Api, Any]:
            conn = connect(settings.db_path)
            migrate(conn)
            return Api(conn, settings), conn

        def _send(self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data: Any, status: int = 200) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            try:
                data = json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ApiError("JSON inválido") from exc
            return data if isinstance(data, dict) else {}

        def _run(self, fn: Callable[[Api, dict[str, str]], Any]) -> None:
            url = urlparse(self.path)
            params = {k: v[-1] for k, v in parse_qs(url.query).items()}
            api, conn = None, None
            try:
                api, conn = self._api()
                result = fn(api, params)
                if isinstance(result, tuple):  # download
                    self._send(200, *result)
                else:
                    self._json(result)
            except ApiError as exc:
                self._json({"error": str(exc)}, exc.status)
            except Exception as exc:  # noqa: BLE001
                self._json({"error": f"erro interno: {exc}"}, 500)
            finally:
                if conn is not None:
                    conn.close()

        # ---------------------------------------------------------- roteamento
        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path == "/" or path == "/index.html":
                body = (STATIC_DIR / "index.html").read_bytes()
                return self._send(200, body, "text/html; charset=utf-8")
            routes: dict[str, Callable[[Api, dict[str, str]], Any]] = {
                "/api/meta": lambda a, p: a.meta(),
                "/api/roadmap": lambda a, p: a.roadmap(),
                "/api/recommend": lambda a, p: a.recommend(p.get("class", "mage"), p.get("faction", "horde"),
                                                           p.get("mode", "pvp")),
                "/api/decision-table": lambda a, p: a.decision_table(),
                "/api/characters": lambda a, p: a.list_characters(),
                "/api/racials": lambda a, p: a.racials(p.get("class") or None),
                "/api/racials/spell-ids": lambda a, p: a.racial_spell_ids(),
                "/api/collection/progress": lambda a, p: a.progress(p.get("character")),
                "/api/collection/items": lambda a, p: a.items(p),
                "/api/collection/export": self._export,
                "/api/professions": lambda a, p: a.professions(),
                "/api/professions/recommend": lambda a, p: a.professions_recommend(p.get("likes", ""), p.get("class"),
                                                                                  p.get("goal")),
            }
            if path in routes:
                return self._run(routes[path])
            self._json({"error": "rota não encontrada"}, 404)

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path.rstrip("/")
            body: dict[str, Any] = {}
            try:
                body = self._body()
            except ApiError as exc:
                return self._json({"error": str(exc)}, 400)
            if path == "/api/characters":
                return self._run(lambda a, p: a.add_character(body))
            if path.startswith("/api/characters/"):
                cid = path.rsplit("/", 1)[-1]
                return self._run(lambda a, p: a.update_character(cid, body))
            if path == "/api/collection/status":
                return self._run(lambda a, p: a.set_item_status(body))
            if path == "/api/racials/update":
                return self._run(lambda a, p: a.update_racials())
            if path == "/api/racials/ingest":
                return self._run(lambda a, p: a.ingest_racials(body))
            if path == "/api/collection/import-att":
                return self._run(lambda a, p: a.import_att())
            self._json({"error": "rota não encontrada"}, 404)

        def do_DELETE(self) -> None:  # noqa: N802
            path = urlparse(self.path).path.rstrip("/")
            if path.startswith("/api/characters/"):
                cid = path.rsplit("/", 1)[-1]
                return self._run(lambda a, p: a.delete_character(cid))
            self._json({"error": "rota não encontrada"}, 404)

        @staticmethod
        def _export(api: Api, params: dict[str, str]) -> tuple[bytes, str, dict[str, str]]:
            fmt = params.get("format", "csv")
            if fmt not in ("csv", "json"):
                raise ApiError("formato deve ser csv ou json")
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / f"colecao.{fmt}"
                CollectionService(api.coll).export(out, fmt)
                data = out.read_bytes()
            name = f"colecao_{date.today():%Y%m%d}.{fmt}"
            ctype = "text/csv; charset=utf-8" if fmt == "csv" else "application/json; charset=utf-8"
            return data, ctype, {"Content-Disposition": f'attachment; filename="{name}"'}

    return Handler


def make_server(settings: Settings, port: int = 8765) -> ThreadingHTTPServer:
    handler = _make_handler(settings)
    last_error: OSError | None = None
    for p in range(port, port + 20):  # tenta portas seguintes se a primeira estiver ocupada
        try:
            return ThreadingHTTPServer(("127.0.0.1", p), handler)
        except OSError as exc:
            last_error = exc
    raise OSError(f"nenhuma porta livre entre {port} e {port + 19}: {last_error}")


def serve(settings: Settings, port: int = 8765, open_browser: bool = True) -> None:
    conn = connect(settings.db_path)
    migrate(conn)
    conn.close()
    httpd = make_server(settings, port)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"Interface rodando em {url}  (feche esta janela ou aperte Ctrl+C para parar)")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrado.")
    finally:
        httpd.server_close()
