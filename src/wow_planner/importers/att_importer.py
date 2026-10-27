"""Importador do All The Things (ATT).

Métodos suportados (ver README):
  1. SavedVariables local (AllTheThings.lua) — leitura direta do arquivo que o WoW grava ao
     deslogar ou dar /reload. Estrutura NÃO documentada pelo ATT: usa mapeamento YAML.
  2. Exportação manual em CSV/JSON — colunas mapeadas por `manual_columns` no mesmo YAML.

Não existe API web pública do ATT; este módulo não faz nenhuma chamada de rede.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from wow_planner.importers.lua_table import get_path, parse_savedvariables
from wow_planner.models.character import Character
from wow_planner.models.collection import CATEGORIES, CollectionItem
from wow_planner.repositories.collection_repo import ACCOUNT, CollectionRepository

DEFAULT_MAPPING = Path(__file__).parent / "att_mapping.default.yaml"


@dataclass
class ImportReport:
    file: str
    method: str
    items_seen: int = 0
    items_new: int = 0
    collected: int = 0
    characters_matched: list[str] = field(default_factory=list)
    characters_unmatched: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def load_mapping(path: Path | None = None) -> dict[str, Any]:
    data = yaml.safe_load((path or DEFAULT_MAPPING).read_text(encoding="utf-8")) or {}
    data.setdefault("rules", [])
    data.setdefault("manual_columns", {})
    data.setdefault("category_aliases", {})
    return data


def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v > 0
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "sim", "collected", "obtido"}
    if isinstance(v, (dict, list)):
        return len(v) > 0
    return False


def _norm_category(raw: str, aliases: dict[str, str]) -> str:
    c = str(raw).strip().lower()
    c = aliases.get(c, c)
    return c if c in CATEGORIES else "other"


def _id_table(value: Any) -> dict[str, Any] | None:
    """Converte {id=val} ou lista de ids em dict id->valor."""
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    if isinstance(value, list):
        return {str(v): True for v in value if isinstance(v, (int, str))}
    return None


class ATTImporter:
    def __init__(self, repo: CollectionRepository, mapping: dict[str, Any] | None = None):
        self.repo = repo
        self.mapping = mapping or load_mapping()

    # ------------------------------------------------------------------ inspeção
    @staticmethod
    def inspect(path: Path, max_children: int = 40) -> tuple[list[tuple[str, str, int]], list[str]]:
        """Lista (caminho, tipo, tamanho) das tabelas de 1º e 2º nível do arquivo."""
        res = parse_savedvariables(path.read_text(encoding="utf-8", errors="replace"))
        rows: list[tuple[str, str, int]] = []
        for gname, gval in res.globals.items():
            rows.append((gname, type(gval).__name__, len(gval) if hasattr(gval, "__len__") else 0))
            if isinstance(gval, dict):
                for i, (k, v) in enumerate(gval.items()):
                    if i >= max_children:
                        rows.append((f"{gname}.…", "", len(gval) - max_children))
                        break
                    rows.append((f"{gname}.{k}", type(v).__name__, len(v) if hasattr(v, "__len__") else 0))
        return rows, res.warnings

    # ------------------------------------------------------------------ SavedVariables
    def import_savedvariables(self, path: Path, characters: list[Character]) -> ImportReport:
        report = ImportReport(file=str(path), method="savedvariables")
        if not path.is_file():
            report.warnings.append(f"arquivo não encontrado: {path}")
            return report
        parsed = parse_savedvariables(path.read_text(encoding="utf-8", errors="replace"))
        report.warnings.extend(parsed.warnings)
        if not self.mapping.get("verified"):
            report.warnings.append(
                "mapeamento ATT marcado como NÃO verificado — confira os caminhos com `wow-plan collection inspect-att`."
            )
        by_name = {c.name.lower(): c for c in characters}

        for rule in self.mapping["rules"]:
            rpath = str(rule["path"]).split(".")
            category = _norm_category(rule["category"], self.mapping["category_aliases"])
            node = get_path(parsed.globals, rpath)
            if node is None:
                report.warnings.append(f"regra '{rule['path']}': caminho não existe neste arquivo")
                continue
            if rule.get("scope", "account") == "character":
                if not isinstance(node, dict):
                    report.warnings.append(f"regra '{rule['path']}': esperado mapa de personagens")
                    continue
                for char_key, char_data in node.items():
                    # ATT 5.x: chave = GUID do personagem, nome em char_data.name.
                    # Fallback: chave no formato "Nome-Reino" (versões antigas / exports).
                    att_name = char_data.get("name") if isinstance(char_data, dict) else None
                    short = str(att_name or str(char_key).split("-")[0]).lower()
                    ch = by_name.get(short)
                    if ch is None:
                        label = str(att_name or char_key)
                        if isinstance(char_data, dict):
                            extra = " ".join(str(char_data[k]) for k in ("race", "class", "realm") if char_data.get(k))
                            label = f"{label} ({extra})" if extra else label
                        if label not in report.characters_unmatched:
                            report.characters_unmatched.append(label)
                        continue
                    sub = get_path(char_data, str(rule.get("sub_path", "")).split(".")) if rule.get("sub_path") else char_data
                    table = _id_table(sub)
                    if table is None:
                        continue
                    if ch.name not in report.characters_matched:
                        report.characters_matched.append(ch.name)
                    self._apply(table, category, ch.id or ACCOUNT, report)
            else:
                table = _id_table(node)
                if table is None:
                    report.warnings.append(f"regra '{rule['path']}': não é uma tabela de ids")
                    continue
                self._apply(table, category, ACCOUNT, report)

        self.repo.commit()
        self.repo.log_import("att", str(path), report.items_seen, report.items_new, report.warnings)
        return report

    def _apply(self, table: dict[str, Any], category: str, char_id: int, report: ImportReport) -> None:
        for ext_id, val in table.items():
            name = None
            if isinstance(val, dict):  # algumas tabelas guardam {name=..., collected=...}
                name = val.get("name") or val.get("text")
                val = val.get("collected", val.get("saved", True))
            item = CollectionItem(
                external_source="att", external_id=ext_id, category=category,
                name=str(name) if name else f"{category} #{ext_id}",
                data_source="ATT SavedVariables",
            )
            item_id, created = self._upsert_keep_name(item)
            report.items_seen += 1
            report.items_new += created
            if _truthy(val):
                self.repo.set_status(item_id, "collected", char_id)
                report.collected += 1

    def _upsert_keep_name(self, item: CollectionItem) -> tuple[int, bool]:
        """Não sobrescreve um nome já enriquecido (via CSV) com o placeholder 'categoria #id'."""
        existing = self.repo.conn.execute(
            "SELECT * FROM collection_items WHERE external_source='att' AND category=? AND external_id=?",
            (item.category, item.external_id),
        ).fetchone()
        if existing and item.name.startswith(f"{item.category} #"):
            return existing["id"], False
        return self.repo.upsert_item(item)

    # ------------------------------------------------------------------ export manual
    def import_manual(self, path: Path, character: Character | None = None) -> ImportReport:
        report = ImportReport(file=str(path), method="manual")
        try:
            records = self._read_records(path)
        except Exception as exc:  # noqa: BLE001
            report.warnings.append(f"não foi possível ler {path.name}: {exc}")
            return report
        cols = self.mapping["manual_columns"]
        aliases = self.mapping["category_aliases"]
        char_id = character.id if character and character.id else ACCOUNT
        for n, rec in enumerate(records, start=1):
            if not isinstance(rec, dict):
                report.warnings.append(f"registro {n}: formato inválido, ignorado")
                continue
            norm = {field_: next((rec[c] for c in candidates if c in rec and rec[c] not in ("", None)), None)
                    for field_, candidates in cols.items()}
            if norm.get("external_id") is None or norm.get("category") is None:
                report.warnings.append(f"registro {n}: sem id ou categoria, ignorado")
                continue
            category = _norm_category(norm["category"], aliases)
            try:
                item = CollectionItem(
                    external_source="att",
                    external_id=str(norm["external_id"]),
                    name=str(norm.get("name") or f"{category} #{norm['external_id']}"),
                    category=category,
                    faction=(str(norm["faction"]).lower() if norm.get("faction") else None),
                    source=norm.get("source"),
                    class_restriction=norm.get("class_restriction"),
                    race_restriction=norm.get("race_restriction"),
                    expansion=norm.get("expansion") or "forever",
                    difficulty=norm.get("difficulty"),
                    priority=int(norm.get("priority") or 3),
                    data_source=f"ATT export manual ({path.name})",
                )
            except Exception as exc:  # noqa: BLE001
                report.warnings.append(f"registro {n}: inválido ({exc})")
                continue
            item_id, created = self.repo.upsert_item(item)
            report.items_seen += 1
            report.items_new += created
            st = norm.get("status")
            if st is not None:
                status = "collected" if _truthy(st) else (
                    str(st).lower() if str(st).lower() in {"in_progress", "blocked"} else "missing")
                self.repo.set_status(item_id, status, char_id)
                report.collected += status == "collected"
        self.repo.commit()
        self.repo.log_import("att-manual", str(path), report.items_seen, report.items_new, report.warnings)
        return report

    @staticmethod
    def _read_records(path: Path) -> list[Any]:
        if path.suffix.lower() == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key in ("items", "things", "data"):
                    if isinstance(data.get(key), list):
                        return data[key]
                raise ValueError("JSON deve ser lista ou ter chave 'items'")
            return data
        with path.open(newline="", encoding="utf-8-sig") as fh:
            sample = fh.read(2048)
            fh.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t") if sample else csv.excel
            return list(csv.DictReader(fh, dialect=dialect))
