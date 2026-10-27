"""HTTP e utilitários compartilhados pelas fontes ao vivo."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Callable

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0 Safari/537.36 wow-collection-planner/0.4")

Fetcher = Callable[[str], Any]


def http_get(url: str, timeout: float = 15.0, accept: str = "application/json") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def http_get_json(url: str, timeout: float = 15.0) -> Any:
    return json.loads(http_get(url, timeout).decode("utf-8"))


def http_get_text(url: str, timeout: float = 15.0) -> str:
    return http_get(url, timeout, accept="application/rss+xml, application/xml, text/xml, */*").decode("utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def err_text(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return f"sem conexão: {exc.reason}"
    return type(exc).__name__ + (f": {exc}" if str(exc) else "")


class LeafTextParser(HTMLParser):
    """Extrai linhas de texto de <li> e <p> que não contêm outros <li> (itens de patch notes)."""

    BLOCKS = {"li", "p", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[list[str]] = []
        self.has_child_li: list[bool] = []
        self.lines: list[tuple[str, str]] = []  # (tag, texto)

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "br" and self.stack:
            self.stack[-1].append(" ")
        if tag in self.BLOCKS:
            if tag == "li" and self.has_child_li:
                self.has_child_li[-1] = True
            self.stack.append([])
            self.has_child_li.append(False)

    def handle_endtag(self, tag: str) -> None:
        if tag in self.BLOCKS and self.stack:
            buf = self.stack.pop()
            nested = self.has_child_li.pop()
            text = re.sub(r"\s+", " ", "".join(buf)).strip()
            if text and not nested:
                self.lines.append((tag, text))
            if self.stack and nested is False and tag != "li":
                self.stack[-1].append(" ")

    def handle_data(self, data: str) -> None:
        if self.stack:
            self.stack[-1].append(data)


def leaf_lines(html: str) -> list[tuple[str, str]]:
    p = LeafTextParser()
    p.feed(html or "")
    return p.lines
