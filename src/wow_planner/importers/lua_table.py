"""Parser tolerante de arquivos SavedVariables (subconjunto de Lua usado pelo WoW).

O WoW grava SavedVariables como atribuições globais de tabelas Lua:

    NomeGlobal = {
        ["chave"] = "valor",
        [123] = true,
        { 1, 2, 3 },
    }

Este parser NÃO executa Lua: só lê literais (tabelas, strings, números, booleanos, nil).
Se uma atribuição estiver corrompida, ela é pulada com um aviso e as demais continuam.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER = re.compile(r"-?(0[xX][0-9a-fA-F]+|(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?)")
_TOPLEVEL = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=", re.MULTILINE)
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'", "\n": "\n", "0": "\0"}


class LuaParseError(ValueError):
    pass


@dataclass
class ParseResult:
    globals: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class _Parser:
    def __init__(self, text: str, pos: int = 0):
        self.s = text
        self.i = pos
        self.n = len(text)

    def error(self, msg: str) -> LuaParseError:
        line = self.s.count("\n", 0, self.i) + 1
        return LuaParseError(f"linha {line}: {msg}")

    def skip(self) -> None:
        while self.i < self.n:
            c = self.s[self.i]
            if c in " \t\r\n,;":  # vírgulas/; separadores tratados como espaço dentro de tabelas
                self.i += 1
            elif self.s.startswith("--[[", self.i):
                end = self.s.find("]]", self.i + 4)
                self.i = self.n if end < 0 else end + 2
            elif self.s.startswith("--", self.i):
                end = self.s.find("\n", self.i)
                self.i = self.n if end < 0 else end + 1
            else:
                break

    def value(self) -> Any:
        self.skip()
        if self.i >= self.n:
            raise self.error("fim inesperado")
        c = self.s[self.i]
        if c == "{":
            return self.table()
        if c in "\"'":
            return self.string()
        if self.s.startswith("[[", self.i) or self.s.startswith("[=", self.i):
            return self.long_string()
        m = _NUMBER.match(self.s, self.i)
        if m and (c.isdigit() or c in "-."):
            self.i = m.end()
            tok = m.group(0)
            if "x" in tok.lower():
                return int(tok, 16)
            return float(tok) if any(ch in tok for ch in ".eE") else int(tok)
        m = _IDENT.match(self.s, self.i)
        if m:
            word = m.group(0)
            self.i = m.end()
            if word == "true":
                return True
            if word == "false":
                return False
            if word == "nil":
                return None
            raise self.error(f"identificador inesperado '{word}'")
        raise self.error(f"caractere inesperado '{c}'")

    def string(self) -> str:
        quote = self.s[self.i]
        self.i += 1
        out = []
        while self.i < self.n:
            c = self.s[self.i]
            if c == "\\":
                nxt = self.s[self.i + 1] if self.i + 1 < self.n else ""
                if nxt.isdigit():
                    m = re.match(r"\d{1,3}", self.s[self.i + 1:])
                    out.append(chr(int(m.group(0))))  # type: ignore[union-attr]
                    self.i += 1 + len(m.group(0))  # type: ignore[union-attr]
                    continue
                out.append(_ESCAPES.get(nxt, nxt))
                self.i += 2
                continue
            if c == quote:
                self.i += 1
                return "".join(out)
            out.append(c)
            self.i += 1
        raise self.error("string não terminada")

    def long_string(self) -> str:
        m = re.match(r"\[(=*)\[", self.s[self.i:])
        if not m:
            raise self.error("long string inválida")
        close = "]" + m.group(1) + "]"
        start = self.i + m.end()
        end = self.s.find(close, start)
        if end < 0:
            raise self.error("long string não terminada")
        self.i = end + len(close)
        return self.s[start:end]

    def table(self) -> dict[Any, Any] | list[Any]:
        self.i += 1  # {
        result: dict[Any, Any] = {}
        array_index = 1
        while True:
            self.skip()
            if self.i >= self.n:
                raise self.error("tabela não terminada")
            if self.s[self.i] == "}":
                self.i += 1
                break
            if self.s[self.i] == "[" and not self.s.startswith("[[", self.i) and not self.s.startswith("[=", self.i):
                self.i += 1
                key = self.value()
                self.skip()
                if self.s[self.i] != "]":
                    raise self.error("esperado ']'")
                self.i += 1
                self._expect("=")
                result[key] = self.value()
                continue
            m = _IDENT.match(self.s, self.i)
            if m and re.match(r"\s*=(?!=)", self.s[m.end():]):
                self.i = m.end()
                self._expect("=")
                result[m.group(0)] = self.value()
                continue
            result[array_index] = self.value()
            array_index += 1
        # tabelas puramente sequenciais viram listas
        if result and all(isinstance(k, int) for k in result) and sorted(result) == list(range(1, len(result) + 1)) \
                and array_index > 1:
            return [result[k] for k in range(1, len(result) + 1)]
        return result

    def _expect(self, ch: str) -> None:
        self.skip()
        if self.i >= self.n or self.s[self.i] != ch:
            raise self.error(f"esperado '{ch}'")
        self.i += 1


def parse_savedvariables(text: str) -> ParseResult:
    """Lê todas as atribuições globais; erros em uma não impedem as outras."""
    res = ParseResult()
    matches = list(_TOPLEVEL.finditer(text))
    consumed = 0
    for m in matches:
        name = m.group(1)
        if m.start() < consumed or text.startswith("=", m.end()):  # dentro de tabela já lida / '=='
            continue
        try:
            p = _Parser(text, m.end())
            res.globals[name] = p.value()
            consumed = p.i
        except (LuaParseError, IndexError, AttributeError) as exc:
            res.warnings.append(f"{name}: não foi possível ler ({exc}); ignorado")
    if not matches:
        res.warnings.append("nenhuma variável global encontrada — o arquivo é mesmo um SavedVariables?")
    return res


def get_path(data: Any, path: list[str]) -> Any:
    cur = data
    for part in path:
        if isinstance(cur, dict):
            if part in cur:
                cur = cur[part]
            elif part.lstrip("-").isdigit() and int(part) in cur:
                cur = cur[int(part)]
            else:
                return None
        elif isinstance(cur, list) and part.isdigit() and 0 < int(part) <= len(cur):
            cur = cur[int(part) - 1]
        else:
            return None
    return cur
