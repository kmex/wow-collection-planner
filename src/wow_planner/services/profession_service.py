"""Guia de profissões: sugere duplas de profissões a partir do que o jogador gosta de craftar.

Pontuação (análise do app, não oficial):
- +10 por interesse coberto pela dupla (o motivo principal);
- bônus de sinergia da dupla (coletor que abastece o craft) x2;
- encaixe na classe e no objetivo (0-3 cada, de professions.yaml);
- -4 se um craft precisa de coleta e a dupla não tem o coletor (você vai depender do leilão).
Interesses que só secundárias cobrem (Cooking, First Aid, Fishing) nunca gastam slot primário.
"""

from __future__ import annotations

from functools import lru_cache
from itertools import combinations
from pathlib import Path
from typing import Any

import yaml

DATA_FILE = Path(__file__).resolve().parent.parent / "game_data" / "data" / "professions.yaml"
CLASSES = ("mage", "priest", "paladin")
GOALS = ("pvp", "pve", "collection")


class ProfessionError(ValueError):
    pass


@lru_cache(maxsize=4)
def _load(path: str) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    data.pop("_refs", None)
    return data


def load_professions(override_dir: Path | None = None) -> dict[str, Any]:
    path = override_dir / "professions.yaml" if override_dir and (override_dir / "professions.yaml").is_file() else DATA_FILE
    data = _load(str(path))
    problems = validate(data)
    if problems:
        raise ProfessionError("professions.yaml inválido:\n- " + "\n- ".join(problems))
    return data


def validate(data: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    profs = {p["id"]: p for p in data.get("professions", [])}
    for p in profs.values():
        if not p.get("refs"):
            problems.append(f"{p['id']}: sem fonte (refs)")
        for r in p.get("refs", []):
            if not r.get("source") or not r.get("last_verified"):
                problems.append(f"{p['id']}: ref sem source/last_verified")
    for i in data.get("interests", []):
        for pid in i["by"]:
            if pid not in profs:
                problems.append(f"interesse {i['id']}: profissão desconhecida {pid}")
    for pair in data.get("pairs", []):
        for pid in (pair["a"], pair["b"]):
            if pid not in profs:
                problems.append(f"dupla {pair['a']}+{pair['b']}: profissão desconhecida {pid}")
    return problems


def _norm_likes(data: dict[str, Any], likes: list[str]) -> list[str]:
    known = {i["id"]: i for i in data["interests"]}
    alias = {"bag": "bags", "bolsa": "bags", "bolsas": "bags", "elixir": "elixirs", "elixires": "elixirs",
             "flask": "flasks", "frasco": "flasks", "frascos": "flasks", "potion": "mana_potions",
             "pocao": "mana_potions", "poções": "mana_potions", "pocoes": "mana_potions",
             "enchant": "enchants", "encantamento": "enchants", "encantamentos": "enchants",
             "wand": "wands", "varinha": "wands", "varinhas": "wands", "comida": "food",
             "atadura": "bandages", "ataduras": "bandages", "pvp": "gadgets_pvp", "gadgets": "gadgets_pvp",
             "ouro": "gathering_gold", "gold": "gathering_gold", "armas": "weapons", "arma": "weapons"}
    out: list[str] = []
    for raw in likes:
        key = raw.strip().lower().replace(" ", "_").replace("-", "_")
        if not key:
            continue
        key = alias.get(key, key)
        if key not in known:
            raise ProfessionError(f"interesse desconhecido: {raw!r}. Use: {', '.join(known)}")
        if key not in out:
            out.append(key)
    return out


def _pair_bonus(data: dict[str, Any], a: str, b: str) -> tuple[int, str | None]:
    for p in data["pairs"]:
        if {p["a"], p["b"]} == {a, b}:
            return int(p["bonus"]), p["why"]
    return 0, None


def recommend_professions(likes: list[str], class_id: str | None = None, goal: str | None = None,
                          top: int = 3, data: dict[str, Any] | None = None) -> dict[str, Any]:
    data = data or load_professions()
    if class_id and class_id not in CLASSES:
        raise ProfessionError(f"classe desconhecida: {class_id}")
    if goal and goal not in GOALS:
        raise ProfessionError(f"objetivo desconhecido: {goal}")
    likes = _norm_likes(data, likes)
    profs = {p["id"]: p for p in data["professions"]}
    interests = {i["id"]: i for i in data["interests"]}
    primaries = [p for p in profs.values() if p["kind"] == "primary"]

    def covers(pid: str, like: str) -> bool:
        return pid in interests[like]["by"]

    secondary_likes = [l for l in likes if all(profs[p]["kind"] == "secondary" for p in interests[l]["by"])]
    primary_likes = [l for l in likes if l not in secondary_likes]

    pairs = []
    for a, b in combinations([p["id"] for p in primaries], 2):
        covered = [l for l in primary_likes if covers(a, l) or covers(b, l)]
        bonus, why = _pair_bonus(data, a, b)
        score = 10 * len(covered) + 2 * bonus
        fit = []
        for pid in (a, b):
            p = profs[pid]
            if class_id:
                score += p["class_fit"].get(class_id, 0)
            if goal:
                score += p["goal_fit"].get(goal, 0)
            g = p.get("gatherer")
            if g and g not in (a, b):
                score -= 4
                fit.append(f"{p['name']} vai precisar comprar material ({profs[g]['name']}) no leilão ou de um alt.")
        if not primary_likes:  # sem interesse de craft: privilegia sinergia/ouro
            score += 5 * bonus
        pairs.append({"a": a, "b": b, "score": score, "covered": covered,
                      "uncovered": [l for l in primary_likes if l not in covered], "synergy": why, "caveats": fit})
    pairs.sort(key=lambda x: (-x["score"], x["a"], x["b"]))

    def view(p: dict[str, Any]) -> dict[str, Any]:
        reasons = [f"Faz {interests[l]['label'].lower()} ({', '.join(profs[x]['name'] for x in (p['a'], p['b']) if covers(x, l))})."
                   for l in p["covered"]]
        if p["synergy"]:
            reasons.append(p["synergy"])
        return {"professions": [profs[p["a"]]["name"], profs[p["b"]]["name"]], "ids": [p["a"], p["b"]],
                "score": p["score"], "reasons": reasons, "caveats": p["caveats"],
                "covered": [interests[l]["label"] for l in p["covered"]],
                "uncovered": [interests[l]["label"] for l in p["uncovered"]]}

    best = [view(p) for p in pairs[:top]]
    return {
        "likes": [{"id": l, "label": interests[l]["label"], "by": [profs[p]["name"] for p in interests[l]["by"]],
                   "note": interests[l].get("note")} for l in likes],
        "class_id": class_id, "goal": goal,
        "pairs": best,
        "secondary": [{"id": s, "name": profs[s]["name"],
                       "why": ("Cobre: " + ", ".join(interests[l]["label"] for l in likes if covers(s, l)))
                       if any(covers(s, l) for l in likes) else "vale ter em todo personagem."}
                      for s in data["rules"]["secondary"]],
        "account_plan": account_plan(likes, data) if best and best[0]["uncovered"] else None,
        "version": data.get("version"),
    }


def account_plan(likes: list[str], data: dict[str, Any] | None = None,
                 classes: tuple[str, ...] = CLASSES) -> list[dict[str, Any]]:
    """Divide interesses que não cabem em 2 profissões entre os personagens da conta (main + alts)."""
    data = data or load_professions()
    likes = _norm_likes(data, likes)
    profs = {p["id"]: p for p in data["professions"]}
    interests = {i["id"]: i for i in data["interests"]}
    needed: list[str] = []
    for l in likes:
        opts = [p for p in interests[l]["by"] if profs[p]["kind"] == "primary"]
        if opts and not any(o in needed for o in opts):
            needed.append(opts[0])
    plan: dict[str, list[str]] = {c: [] for c in classes}
    for pid in sorted(needed, key=lambda p: -max(profs[p]["class_fit"].get(c, 0) for c in classes)):
        for c in sorted(classes, key=lambda c: (-profs[pid]["class_fit"].get(c, 0), len(plan[c]))):
            if len(plan[c]) < 2:
                plan[c].append(pid)
                break
    out = []
    for c in classes:
        chosen = plan[c]
        if len(chosen) == 1:  # completa com o coletor do craft ou a melhor dupla
            g = profs[chosen[0]].get("gatherer")
            if g:
                chosen.append(g)
            else:
                best = max((p for p in data["pairs"] if chosen[0] in (p["a"], p["b"])),
                           key=lambda p: p["bonus"], default=None)
                if best:
                    other = best["b"] if best["a"] == chosen[0] else best["a"]
                    if other not in chosen:
                        chosen.append(other)
        if chosen:
            out.append({"class_id": c, "professions": [profs[p]["name"] for p in chosen],
                        "crafts": [interests[l]["label"] for l in likes if any(p in interests[l]["by"] for p in chosen)]})
    return out


def profession_details(data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    data = data or load_professions()
    interests = {i["id"]: i["label"] for i in data["interests"]}
    out = []
    for p in data["professions"]:
        out.append({**{k: v for k, v in p.items() if k != "refs"},
                    "crafts_labels": [interests.get(c, c) for c in p.get("crafts", [])],
                    "sources": [{"url": r["source"], "last_verified": r["last_verified"],
                                 "confidence": r.get("confidence")} for r in p["refs"]]})
    return out
