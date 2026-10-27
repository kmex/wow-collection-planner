"""Camada de API da interface web: funções puras que devolvem dicts JSON-serializáveis."""

from __future__ import annotations

import sqlite3
from typing import Any

from wow_planner.config import Settings
from wow_planner.game_data import load_game_data
from wow_planner.importers import ATTImporter, load_mapping
from wow_planner.live import (SOURCES, ensure_seeds, ingest_tooltips, known_spell_ids, last_runs, last_update,
                              live_map, news, notes_by_racial, pick_variant, recent_changes, same_text,
                              tooltip_status, update_all, wowhead_tooltips)
from wow_planner.models.character import STATUS_LABELS_PT, Character, CharacterStatus, Faction, Goal
from wow_planner.models.collection import CATEGORIES, ItemStatus, ProgressRow
from wow_planner.models.game import GameDataBundle
from wow_planner.repositories import CharacterRepository, CollectionRepository, ItemFilter
from wow_planner.services import (CollectionService, ProfessionError, RecommendationError, build_roadmap, decision_table,
                                  load_professions, profession_details, recommend, recommend_professions)
from wow_planner.services.recommendation_service import CLASS_ALIASES, FACTION_ALIASES, normalize


class ApiError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _progress(p: ProgressRow) -> dict[str, Any]:
    return {"scope": p.scope, "total": p.total, "collected": p.collected, "in_progress": p.in_progress,
            "blocked": p.blocked, "missing": p.missing, "obtainable": p.obtainable, "percent": round(p.percent, 1)}


def _char(c: Character) -> dict[str, Any]:
    d = c.model_dump(mode="json")
    d["status_label"] = STATUS_LABELS_PT[c.status]
    return d


class Api:
    def __init__(self, conn: sqlite3.Connection, settings: Settings, bundle: GameDataBundle | None = None):
        self.conn = conn
        self.settings = settings
        self.bundle = bundle or load_game_data(settings.data_dir if settings.data_dir.is_dir() else None)
        self.chars = CharacterRepository(conn)
        self.coll = CollectionRepository(conn)
        self.svc = CollectionService(self.coll)
        ensure_seeds(conn)
        self.live = live_map(conn)
        self.notes = notes_by_racial(conn)
        self.wh = wowhead_tooltips(conn)

    def _en(self, racial_id: str, class_id: str | None) -> dict[str, Any] | None:
        """Tooltip oficial (inglês) da variante certa + aviso de mudança anunciada pela Blizzard."""
        data = self.live.get(racial_id)
        v = pick_variant(data["variants"], class_id) if data else None
        notes = self._notes(racial_id, v)
        # vale só a nota mais recente que fala de valores (uma nota antiga já aplicada não gera alerta)
        latest = next((n for n in notes if n["tooltip_status"] in ("outdated", "removed", "applied")), None)
        alert = latest if latest and latest["tooltip_status"] in ("outdated", "removed") else None
        if not v:
            return {"missing": True, "notice": alert, "notes": notes[:3]} if notes else None
        wh = self.wh.get(v["spell_id"])
        return v | {
            "fetched_at": data["fetched_at"], "method": data["method"],
            "url": f"https://forever.warcraftdb.com/data/spell/{v['spell_id']}",
            "wowhead": (wh | {"same": same_text(wh.get("description"), v.get("description")),
                              "url": f"https://www.wowhead.com/forever/spell={v['spell_id']}"}) if wh else None,
            "notice": alert, "notes": notes[:3],
        }

    def _notes(self, racial_id: str, variant: dict[str, Any] | None) -> list[dict[str, Any]]:
        out = []
        try:
            own = self.bundle.racial(racial_id).name.lower().rstrip("!").replace("'", "")
        except KeyError:
            own = racial_id
        for n in self.notes.get(racial_id, []):
            st = tooltip_status(n["text"], variant)
            if st and st["status"] == "removed" and own not in n["text"].lower().replace("’", "").replace("'", ""):
                st = {"status": "replaces"}  # nota fala da racial antiga que esta substitui
            out.append({k: n[k] for k in ("text", "url", "posted_at", "edited_at", "region", "topic_title", "post_number")}
                       | {"tooltip_status": st["status"] if st else None, "change": st})
        return out

    # ------------------------------------------------------------------ meta
    def meta(self) -> dict[str, Any]:
        att = self.settings.resolve_att_file()
        return {
            "game_data_version": self.bundle.version,
            "game": self.bundle.game,
            "db_path": str(self.settings.db_path),
            "att_file": str(att) if att else None,
            "att_file_exists": bool(att and att.is_file()),
            "categories": CATEGORIES,
            "races": [{"id": r.id, "name": r.name, "faction": r.faction, "classes": r.classes,
                       "requires_purchase": r.requires_purchase, "notes": r.notes} for r in self.bundle.races],
            "classes": sorted({c for r in self.bundle.races for c in r.classes}),
            "grades_source": self.bundle.general_grades_ref.source if self.bundle.general_grades_ref else None,
        }

    # ------------------------------------------------------------------ planejamento
    def roadmap(self) -> list[dict[str, Any]]:
        out = []
        for e in build_roadmap(self.bundle, self.chars.list()):
            st = e.step
            out.append({
                "order": st.order, "title": st.title, "class_id": st.class_id, "faction": st.faction,
                "mode": st.mode, "condition": st.condition,
                "plans": [{"faction": p.faction, "best_by_mode": p.best_by_mode,
                           "key_racials": [{"name": n, "effect": eff, "id": rid, "en": self._en(rid, st.class_id)}
                                           for n, eff, rid in p.key_racials],
                           "starting_spec": p.starting_spec, "professions": p.professions} for p in e.plans],
                "risks": e.risks,
                "existing": [_char(c) for c in e.existing],
                "source": st.ref.source, "last_verified": st.ref.last_verified,
            })
        return out

    def recommend(self, class_id: str, faction: str, mode: str) -> dict[str, Any]:
        try:
            res = recommend(self.bundle, class_id, faction, mode)
        except RecommendationError as exc:
            raise ApiError(str(exc)) from exc
        rec = res.rec
        return {
            "class_id": rec.class_id, "faction": rec.faction, "mode": rec.mode,
            "starting_spec": rec.starting_spec, "professions": rec.professions, "notes": rec.notes,
            "warnings": res.excluded_notes,
            "source": rec.ref.source, "confidence": rec.ref.confidence, "last_verified": rec.ref.last_verified,
            "picks": [{
                "race_id": p.race_id, "race_name": p.race_name, "rank": p.rank, "reason": p.reason,
                "grade": p.general_grade, "requires_purchase": p.requires_purchase,
                "racials": [{"id": r.id, "name": r.name, "kind": r.kind, "cooldown": r.cooldown, "effect": r.effect,
                             "source": r.ref.source, "en": self._en(r.id, rec.class_id)} for r in p.key_racials],
            } for p in res.picks],
        }

    def decision_table(self) -> list[dict[str, str]]:
        return decision_table(self.bundle)

    # ------------------------------------------------------------------ racials (ao vivo)
    def racials(self, class_id: str | None = None) -> dict[str, Any]:
        races_by_racial: dict[str, list[dict[str, str]]] = {}
        for race in self.bundle.races:
            for rid in race.racials:
                races_by_racial.setdefault(rid, []).append({"id": race.id, "name": race.name, "faction": race.faction})
        items = []
        for r in self.bundle.racials:
            if class_id and r.classes and class_id not in r.classes:
                continue
            data = self.live.get(r.id) or {"variants": []}
            en = self._en(r.id, class_id)
            items.append({
                "id": r.id, "name": r.name, "kind": r.kind, "effect_pt": r.effect,
                "notes": self._notes(r.id, pick_variant(data["variants"], class_id)),
                "source": r.ref.source, "last_verified": r.ref.last_verified,
                "pvp_value": r.pvp_value, "pve_value": r.pve_value, "class_only": r.classes,
                "races": races_by_racial.get(r.id, []),
                "en": en if en and not en.get("missing") else None, "variants": data["variants"],
            })
        return {"items": items, "last_update": last_update(self.conn), "changes": recent_changes(self.conn, 60),
                "sources": last_runs(self.conn), "news": news(self.conn, 15)}

    def update_racials(self) -> dict[str, Any]:
        return update_all(self.conn, self.bundle)

    def racial_spell_ids(self) -> dict[str, list[int]]:
        return known_spell_ids(self.conn)

    def ingest_racials(self, data: dict[str, Any]) -> dict[str, Any]:
        payload = data.get("items")
        if not isinstance(payload, dict):
            raise ApiError("envie {items: {racial_id: [{spell_id, html}]}}")
        rep = ingest_tooltips(self.conn, payload)
        self.conn.execute("INSERT INTO live_source_runs(source, started_at, finished_at, ok, items, new_items, errors) "
                          "VALUES ('warcraftdb', ?, datetime('now'), 1, ?, ?, ?)",
                          (rep.started_at, rep.updated + rep.unchanged, rep.updated, "\n".join(rep.errors) or None))
        self.conn.commit()
        return rep.as_dict()

    # ------------------------------------------------------------------ personagens
    # ---------------- profissões
    def professions(self) -> dict[str, Any]:
        data = load_professions(self.settings.data_dir if self.settings.data_dir.is_dir() else None)
        return {"version": data.get("version"), "rules": data["rules"], "interests": data["interests"],
                "pairs": data["pairs"], "professions": profession_details(data)}

    def professions_recommend(self, likes: str, class_id: str | None, goal: str | None) -> dict[str, Any]:
        try:
            return recommend_professions([x for x in likes.split(",") if x.strip()], class_id or None, goal or None)
        except ProfessionError as exc:
            raise ApiError(str(exc)) from exc

    def list_characters(self) -> list[dict[str, Any]]:
        return [_char(c) for c in self.chars.list()]

    def _get_char(self, char_id: Any) -> Character:
        try:
            ch = self.chars.get(int(char_id))
        except (TypeError, ValueError):
            ch = None
        if ch is None:
            raise ApiError("personagem não encontrado", 404)
        return ch

    def add_character(self, data: dict[str, Any]) -> dict[str, Any]:
        name = str(data.get("name", "")).strip()
        if not name:
            raise ApiError("informe o nome")
        faction = normalize(str(data.get("faction", "")), FACTION_ALIASES)
        class_id = normalize(str(data.get("class_name", "")), CLASS_ALIASES)
        race_id = str(data.get("race", "")).strip().lower()
        try:
            race = self.bundle.race(race_id)
        except KeyError as exc:
            raise ApiError(f"raça desconhecida: {race_id}") from exc
        if race.faction != faction:
            raise ApiError(f"{race.name} pertence à facção {race.faction}")
        if class_id not in race.classes:
            msg = f"{race.name} não pode ser {class_id}"
            if class_id == "mage" and race.base_race == "skyborne":
                msg += " — Mage Skyborne só existe como High Order Skyborne (Aliança)"
            raise ApiError(msg)
        if self.chars.find_by_name(name):
            raise ApiError(f"já existe um personagem chamado {name}")
        try:
            ch = Character(
                name=name, faction=Faction(faction), race=race_id, class_name=class_id,
                spec=(data.get("spec") or None), level=int(data.get("level") or 1),
                realm=(data.get("realm") or None), goal=Goal(data.get("goal") or "pvp"),
                status=CharacterStatus(data.get("status") or "planned"),
                priority=int(data.get("priority") or 3), creation_order=int(data.get("creation_order") or 99),
                notes=(data.get("notes") or None),
            )
        except ValueError as exc:
            raise ApiError(f"dados inválidos: {exc}") from exc
        return _char(self.chars.add(ch))

    def update_character(self, char_id: Any, data: dict[str, Any]) -> dict[str, Any]:
        ch = self._get_char(char_id)
        status = data.get("status", ch.status.value)
        level = data.get("level")
        try:
            CharacterStatus(status)
            level = int(level) if level not in (None, "") else None
        except ValueError as exc:
            raise ApiError(f"dados inválidos: {exc}") from exc
        if level is not None and not 1 <= level <= 60:
            raise ApiError("nível deve estar entre 1 e 60")
        self.chars.update_status(ch.id, status, level)  # type: ignore[arg-type]
        return _char(self._get_char(ch.id))

    def delete_character(self, char_id: Any) -> dict[str, Any]:
        ch = self._get_char(char_id)
        self.chars.delete(ch.id)  # type: ignore[arg-type]
        return {"deleted": ch.name}

    # ------------------------------------------------------------------ coleção
    def progress(self, character_id: Any = None) -> dict[str, Any]:
        ch = self._get_char(character_id) if character_id not in (None, "", "0") else None
        overall, cats = self.svc.progress(ch)
        chars = self.chars.list()
        return {
            "overall": _progress(overall),
            "categories": [_progress(c) for c in cats],
            "characters": [_progress(p) | {"id": c.id, "class_name": c.class_name, "faction": c.faction.value}
                           for c, p in zip(chars, self.svc.progress_by_character(chars))],
            "alerts": self.svc.restriction_alerts(chars),
        }

    def items(self, params: dict[str, str]) -> list[dict[str, Any]]:
        ch = self._get_char(params["character"]) if params.get("character") not in (None, "", "0") else None
        flt = ItemFilter(
            category=params.get("category") or None,
            faction=normalize(params["faction"], FACTION_ALIASES) if params.get("faction") else None,
            class_name=normalize(params["class"], CLASS_ALIASES) if params.get("class") else None,
            source=params.get("source") or None,
            max_priority=int(params["priority"]) if params.get("priority") else None,
        )
        only = params.get("status") or "missing"
        rows = self.svc.missing(flt, ch) if only == "missing" else self.svc._rows(ch, flt)
        if only not in ("missing", "all"):
            rows = [r for r in rows if r["status"] == only]
        q = (params.get("q") or "").strip().lower()
        if q:
            rows = [r for r in rows if q in r["name"].lower() or q in str(r["external_id"]).lower()]
        keys = ("id", "external_source", "external_id", "name", "category", "source", "faction",
                "class_restriction", "race_restriction", "priority", "status", "progress", "last_verified")
        return [{k: r.get(k) for k in keys} for r in rows[:1000]]

    def set_item_status(self, data: dict[str, Any]) -> dict[str, Any]:
        try:
            item_id = int(data["item_id"])
            status = ItemStatus(data["status"]).value
        except (KeyError, ValueError) as exc:
            raise ApiError("item_id e status válidos são obrigatórios") from exc
        char_id = data.get("character_id")
        ch = self._get_char(char_id) if char_id not in (None, "", 0, "0") else None
        progress = data.get("progress")
        self.coll.set_status(item_id, status, ch.id if ch else 0,
                             float(progress) if progress not in (None, "") else None)
        self.coll.commit()
        return {"item_id": item_id, "status": status}

    def import_att(self) -> dict[str, Any]:
        path = self.settings.resolve_att_file()
        if path is None or not path.is_file():
            raise ApiError(
                "Arquivo do ATT ainda não existe. Entre no WoW Forever com o All The Things ligado, "
                "depois saia do jogo ou digite /reload, e tente de novo.", 404)
        report = ATTImporter(self.coll, load_mapping(self.settings.att_mapping_path)).import_savedvariables(
            path, self.chars.list())
        return {"file": report.file, "items_seen": report.items_seen, "items_new": report.items_new,
                "collected": report.collected, "characters_matched": report.characters_matched,
                "characters_unmatched": report.characters_unmatched, "warnings": report.warnings}
