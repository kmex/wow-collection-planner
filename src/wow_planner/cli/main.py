"""CLI `wow-plan`."""

from __future__ import annotations

import sys
from datetime import date

# Windows: console/redirecionamento em cp1252 quebra com █ ⚠ ✔ — força UTF-8 na saída.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and (getattr(_stream, "encoding", "") or "").lower() not in ("utf-8", "utf8"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from wow_planner.config import Settings
from wow_planner.game_data import GameDataError, load_collection_seed, load_game_data
from wow_planner.importers import ATTImporter, load_mapping
from wow_planner.live import ensure_seeds, live_map, notes_by_racial, pick_variant, tooltip_status, update_all
from wow_planner.models.character import STATUS_LABELS_PT, Character, CharacterStatus, Faction, Goal
from wow_planner.models.collection import STATUS_LABELS_PT as ITEM_STATUS_PT
from wow_planner.models.collection import ItemStatus
from wow_planner.repositories import CharacterRepository, CollectionRepository, ItemFilter, connect, migrate
from wow_planner.services import CollectionService, RecommendationError, build_roadmap, decision_table, recommend
from wow_planner.services import game_data_service
from wow_planner.services.recommendation_service import CLASS_ALIASES, FACTION_ALIASES, normalize

app = typer.Typer(help="Planejador de personagens e colecionismo — World of Warcraft: Forever", no_args_is_help=True)
character_app = typer.Typer(help="Cadastro de personagens e planos", no_args_is_help=True)
collection_app = typer.Typer(help="Colecionismo estilo All The Things", no_args_is_help=True)
app.add_typer(character_app, name="character")
app.add_typer(collection_app, name="collection")

console = Console()
MODE_PT = {"pvp": "PvP", "pve": "PvE", "collection": "Colecionismo"}
FACTION_PT = {"horde": "Horda", "alliance": "Aliança"}


def _settings() -> Settings:
    return Settings.load()


def _conn(settings: Settings):
    conn = connect(settings.db_path)
    migrate(conn)
    return conn


def _bundle(settings: Settings):
    try:
        return load_game_data(settings.data_dir if settings.data_dir.is_dir() else None)
    except GameDataError as exc:
        console.print(f"[red]Erro no game data:[/red] {exc}")
        raise typer.Exit(2)


def _character(repo: CharacterRepository, name: Optional[str]) -> Character | None:
    if not name:
        return None
    ch = repo.find_by_name(name)
    if ch is None:
        console.print(f"[red]Personagem '{name}' não encontrado.[/red]")
        raise typer.Exit(1)
    return ch


def _racial_line(live: dict, racial_id: str, class_id: str, name: str, effect_pt: str) -> str:
    """Tooltip oficial em inglês (Warcraft DB) quando disponível; senão o resumo em PT."""
    v = pick_variant(live.get(racial_id, {}).get("variants", []), class_id)
    if not v:
        return f"• {name}: {effect_pt}"
    meta = " · ".join(x for x in (v.get("cast"), f"{v['cooldown']} cooldown" if v.get("cooldown") else None) if x)
    text = v.get("description") or v.get("aura") or effect_pt
    return f"• {v.get('name') or name}" + (f" [{meta}]" if meta else "") + f": {text}"


def _bar(pct: float, width: int = 20) -> str:
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


# =========================================================================== init
@app.command()
def init(seed: bool = typer.Option(True, help="Carregar colecionáveis-semente verificados")) -> None:
    """Cria o banco SQLite, aplica migrations, valida e registra o game data."""
    s = _settings()
    conn = _conn(s)
    bundle = _bundle(s)
    game_data_service.snapshot(conn, bundle, origin="builtin" if not s.data_dir.is_dir() else str(s.data_dir))
    msg = f"Banco pronto em [bold]{s.db_path}[/bold] · game data v{bundle.version}"
    if seed:
        n = CollectionService(CollectionRepository(conn)).seed(load_collection_seed(s.data_dir if s.data_dir.is_dir() else None))
        msg += f" · {n} colecionáveis-semente novos"
    console.print(f"[green]✔[/green] {msg}")
    console.print("Próximos passos: [cyan]wow-plan roadmap[/cyan] e [cyan]wow-plan character add[/cyan]")


# =========================================================================== characters
@character_app.command("add")
def character_add(
    name: str = typer.Option(..., "--name", "-n", prompt="Nome"),
    faction: str = typer.Option(..., "--faction", "-f", prompt="Facção (horde/alliance)"),
    race: str = typer.Option(..., "--race", "-r", prompt="Raça (ex.: undead, orc, skyborne_high_order)"),
    class_name: str = typer.Option(..., "--class", "-c", prompt="Classe (mage/priest/paladin...)"),
    spec: Optional[str] = typer.Option(None, "--spec"),
    level: int = typer.Option(1, "--level", min=1, max=60),
    realm: Optional[str] = typer.Option(None, "--realm"),
    goal: Goal = typer.Option(Goal.PVP, "--goal"),
    status: CharacterStatus = typer.Option(CharacterStatus.PLANNED, "--status"),
    priority: int = typer.Option(3, "--priority", min=1, max=5, help="1 = mais alta"),
    order: int = typer.Option(99, "--order", min=1, help="Ordem de criação"),
    notes: Optional[str] = typer.Option(None, "--notes"),
) -> None:
    """Cadastra um personagem (valida raça x classe x facção pelo game data)."""
    s = _settings()
    bundle = _bundle(s)
    faction_n = normalize(faction, FACTION_ALIASES)
    class_n = normalize(class_name, CLASS_ALIASES)
    race_n = race.strip().lower().replace(" ", "_")
    try:
        race_obj = bundle.race(race_n)
    except KeyError:
        console.print(f"[red]Raça desconhecida '{race}'. Opções:[/red] {', '.join(r.id for r in bundle.races)}")
        raise typer.Exit(1)
    if race_obj.faction != faction_n:
        console.print(f"[red]{race_obj.name} pertence à facção {race_obj.faction}.[/red]")
        raise typer.Exit(1)
    if class_n not in race_obj.classes:
        console.print(f"[red]{race_obj.name} não pode ser {class_n}.[/red] Classes: {', '.join(race_obj.classes)}")
        if class_n == "mage" and race_obj.base_race == "skyborne":
            console.print("[yellow]Mage Skyborne só existe como High Order Skyborne (Aliança).[/yellow]")
        raise typer.Exit(1)
    repo = CharacterRepository(_conn(s))
    ch = repo.add(Character(name=name, faction=Faction(faction_n), race=race_n, class_name=class_n, spec=spec,
                            level=level, realm=realm, goal=goal, status=status, priority=priority,
                            creation_order=order, notes=notes))
    console.print(f"[green]✔[/green] {ch.name} ({race_obj.name} {class_n}, {faction_n}) cadastrado com id {ch.id}.")
    if race_obj.requires_purchase:
        console.print("[yellow]Atenção: Skyborne exige o Skyborne Heroic/Epic Pack ou a Warcraft Forever Collection.[/yellow]")


@character_app.command("list")
def character_list() -> None:
    """Lista personagens por ordem de criação."""
    s = _settings()
    chars = CharacterRepository(_conn(s)).list()
    if not chars:
        console.print("Nenhum personagem. Use [cyan]wow-plan character add[/cyan].")
        return
    t = Table(title="Personagens")
    for col in ("#", "Nome", "Facção", "Raça", "Classe", "Spec", "Nível", "Reino", "Objetivo", "Status", "Prio", "Notas"):
        t.add_column(col)
    for c in chars:
        t.add_row(str(c.creation_order), c.name, c.faction.value, c.race, c.class_name, c.spec or "-", str(c.level),
                  c.realm or "-", MODE_PT[c.goal.value], STATUS_LABELS_PT[c.status], str(c.priority), c.notes or "")
    console.print(t)


@character_app.command("status")
def character_status(name: str, status: CharacterStatus, level: Optional[int] = typer.Option(None, min=1, max=60)) -> None:
    """Atualiza status (planned/in_progress/done) e opcionalmente o nível."""
    s = _settings()
    repo = CharacterRepository(_conn(s))
    ch = _character(repo, name)
    repo.update_status(ch.id, status.value, level)  # type: ignore[union-attr]
    console.print(f"[green]✔[/green] {name}: {STATUS_LABELS_PT[status]}")


@character_app.command("remove")
def character_remove(name: str, yes: bool = typer.Option(False, "--yes", "-y")) -> None:
    """Remove um personagem (e seus status de coleção por personagem)."""
    s = _settings()
    repo = CharacterRepository(_conn(s))
    ch = _character(repo, name)
    if not yes and not typer.confirm(f"Remover {ch.name}?"):  # type: ignore[union-attr]
        raise typer.Abort()
    repo.delete(ch.id)  # type: ignore[union-attr]
    console.print(f"[green]✔[/green] {name} removido.")


# =========================================================================== recommend / roadmap
@app.command("recommend")
def recommend_cmd(
    class_name: str = typer.Option(..., "--class", "-c"),
    faction: str = typer.Option(..., "--faction", "-f"),
    mode: str = typer.Option("pvp", "--mode", "-m", help="pvp | pve | collection"),
) -> None:
    """Melhores raças para classe/facção/modo, com racials e fontes."""
    s = _settings()
    bundle = _bundle(s)
    try:
        res = recommend(bundle, class_name, faction, mode)
    except RecommendationError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    rec = res.rec
    conn_live = live_map(_conn(s))
    t = Table(title=f"{rec.class_id.capitalize()} · {rec.faction} · {MODE_PT[rec.mode]}")
    for col in ("#", "Raça", "Nota geral*", "Por quê", "Racials-chave"):
        t.add_column(col, overflow="fold")
    for p in res.picks:
        racials = "\n".join(_racial_line(conn_live, r.id, rec.class_id, r.name, r.effect) for r in p.key_racials)
        t.add_row(str(p.rank), p.race_name + (" [yellow]($)[/yellow]" if p.requires_purchase else ""),
                  p.general_grade or "-", p.reason, racials)
    console.print(t)
    console.print(f"Spec inicial: [bold]{rec.starting_spec or '-'}[/bold] · Profissões: [bold]{', '.join(rec.professions) or '-'}[/bold]")
    if rec.notes:
        console.print(f"[dim]Obs.: {rec.notes}[/dim]")
    for note in res.excluded_notes:
        console.print(f"[yellow]⚠ {note}[/yellow]")
    gref = bundle.general_grades_ref
    console.print(f"[dim]* Nota geral: {gref.source if gref else '-'} ({gref.last_verified if gref else '-'}). "
                  f"Divisão PvP/PvE: {rec.ref.source} · confiança={rec.ref.confidence} · last_verified={rec.ref.last_verified}[/dim]")


@app.command("roadmap")
def roadmap_cmd() -> None:
    """Mostra o plano: Mage Horda PvP → Mage Skyborne/PvE (Aliança) → Priest → Paladin."""
    s = _settings()
    bundle = _bundle(s)
    conn = _conn(s)
    chars = CharacterRepository(conn).list()
    live = live_map(conn)
    for e in build_roadmap(bundle, chars):
        st = e.step
        lines = []
        if st.condition:
            lines.append(f"[italic]{escape(st.condition)}[/italic]")
        for plan in e.plans:
            if len(e.plans) > 1:
                lines.append(f"[bold underline]{FACTION_PT[plan.faction]}[/bold underline]")
            for mode in ("pvp", "pve", "collection"):
                if mode in plan.best_by_mode:
                    mark = "▶ " if mode == st.mode else "  "
                    lines.append(f"{mark}[bold]{MODE_PT[mode]}:[/bold] {' > '.join(plan.best_by_mode[mode])}")
            if plan.key_racials:
                lines.append(f"[bold]Racials relevantes ({MODE_PT[st.mode]}):[/bold]")
                lines += ["  " + escape(_racial_line(live, rid, st.class_id, n, eff)) for n, eff, rid in plan.key_racials]
            lines.append(f"[bold]Spec inicial:[/bold] {plan.starting_spec or '-'}   "
                         f"[bold]Profissões:[/bold] {', '.join(plan.professions) or '-'}")
        if e.risks:
            lines.append("[bold]Riscos / trade-offs:[/bold]")
            lines += [f"  [yellow]⚠[/yellow] {r}" for r in e.risks]
        if e.existing:
            lines.append("[bold]Seus personagens nesta etapa:[/bold] " + ", ".join(
                f"{c.name} ({c.race}, nv {c.level}, {STATUS_LABELS_PT[c.status]})" for c in e.existing))
        else:
            lines.append("[dim]Nenhum personagem cadastrado para esta etapa ainda.[/dim]")
        lines.append(f"[dim]fonte: {st.ref.source} · last_verified {st.ref.last_verified}[/dim]")
        console.print(Panel("\n".join(lines), title=f"{st.order}. {escape(st.title)} · {FACTION_PT[st.faction]}", expand=True))
    console.print("[dim]($) = exige compra do pack Skyborne. Detalhes: wow-plan recommend --class X --faction Y --mode Z[/dim]")


@app.command("decision-table")
def decision_table_cmd(markdown: bool = typer.Option(False, "--markdown", help="Imprime em Markdown")) -> None:
    """Tabela de decisão Mage/Priest/Paladin × Horda/Aliança × PvP/PvE."""
    s = _settings()
    bundle = _bundle(s)
    rows = decision_table(bundle)
    if markdown:
        from wow_planner.services import decision_table_markdown
        typer.echo(decision_table_markdown(bundle))
        return
    t = Table(title="Tabela de decisão (análise do app — ver fontes no README)", show_lines=True)
    for h in rows[0]:
        t.add_column(h, overflow="fold")
    for r in rows:
        t.add_row(*r.values())
    console.print(t)


@app.command("professions")
def professions_cmd(
    likes: str = typer.Option("", "--likes", "-l", help="O que você gosta de craftar, separado por vírgula "
                              "(bags,elixirs,flasks,enchants,wands,gadgets_pvp,...)"),
    class_name: Optional[str] = typer.Option(None, "--class", "-c", help="mage, priest ou paladin"),
    goal: Optional[str] = typer.Option(None, "--goal", "-g", help="pvp, pve ou collection"),
    list_interests: bool = typer.Option(False, "--list", help="Lista os interesses aceitos"),
) -> None:
    """Guia de profissões: melhores duplas para o que você gosta de craftar."""
    from wow_planner.services import ProfessionError, load_professions, recommend_professions
    data = load_professions()
    if list_interests or not likes:
        t = Table(title=f"Interesses (professions.yaml {data['version']})")
        t.add_column("id"); t.add_column("O quê"); t.add_column("Profissão")
        for i in data["interests"]:
            t.add_row(i["id"], i["label"], ", ".join(i["by"]))
        console.print(t)
        if not likes:
            console.print("Exemplo: [bold]wow-plan professions --likes bags,elixirs --class mage[/]")
        return
    try:
        r = recommend_professions(likes.split(","), normalize(class_name, CLASS_ALIASES) if class_name else None, goal)
    except (ProfessionError, RecommendationError) as exc:
        console.print(f"[red]{escape(str(exc))}[/]")
        raise typer.Exit(1)
    t = Table(title="Melhores duplas de profissões (análise do app)", show_lines=True)
    t.add_column("#"); t.add_column("Dupla"); t.add_column("Por quê", overflow="fold"); t.add_column("Atenção", overflow="fold")
    for n, p in enumerate(r["pairs"], 1):
        why = "\n".join(p["reasons"]) + (f"\n[dim]Não cobre: {', '.join(p['uncovered'])}[/]" if p["uncovered"] else "")
        t.add_row(str(n), " + ".join(p["professions"]), why, "\n".join(p["caveats"]) or "—")
    console.print(t)
    for like in r["likes"]:
        if like["note"]:
            console.print(f"• [bold]{escape(like['label'])}[/]: {escape(like['note'])}")
    console.print("Secundárias (não gastam slot): " + ", ".join(f"{s['name']}" for s in r["secondary"]))
    if r["account_plan"]:
        console.print("[bold]Não cabe tudo em 2 profissões — divida na conta:[/]")
        for a in r["account_plan"]:
            console.print(f"  {a['class_id'].title()}: {' + '.join(a['professions'])}  ({', '.join(a['crafts']) or 'apoio'})")


# =========================================================================== collection
@collection_app.command("import")
def collection_import(
    source: str = typer.Option("att", "--source", "-s", help="att | csv | json"),
    file: Optional[Path] = typer.Option(None, "--file", help="Arquivo (.lua, .csv ou .json). Padrão: ATT do .env"),
    mapping: Optional[Path] = typer.Option(None, "--mapping", help="YAML de mapeamento ATT"),
    character: Optional[str] = typer.Option(None, "--character", help="Aplicar status a este personagem"),
) -> None:
    """Importa coleção do ATT (SavedVariables ou export manual) ou de CSV/JSON do próprio app."""
    s = _settings()
    conn = _conn(s)
    crepo = CharacterRepository(conn)
    ch = _character(crepo, character)
    repo = CollectionRepository(conn)
    if source == "att":
        path = file or s.resolve_att_file()
        if path is None:
            console.print("[red]Arquivo do ATT não encontrado.[/red] Configure ATT_SAVEDVARIABLES_PATH ou WOW_PATH/WOW_FLAVOR "
                          "no .env, ou passe --file. Veja o README (seção Integração ATT).")
            raise typer.Exit(1)
        importer = ATTImporter(repo, load_mapping(mapping or s.att_mapping_path))
        report = (importer.import_savedvariables(path, crepo.list()) if path.suffix.lower() == ".lua"
                  else importer.import_manual(path, ch))
        console.print(f"[green]✔[/green] ATT ({report.method}) · {path}\n"
                      f"  itens lidos: {report.items_seen} · novos: {report.items_new} · marcados obtidos: {report.collected}")
        if report.characters_matched:
            console.print(f"  personagens casados: {', '.join(report.characters_matched)}")
        if report.characters_unmatched:
            console.print(f"  [yellow]personagens do ATT sem cadastro no app:[/yellow] {', '.join(report.characters_unmatched)}\n"
                          "  → cadastre com `wow-plan character add --name <mesmo nome do jogo>` e importe de novo.")
        for w in report.warnings:
            console.print(f"  [yellow]⚠ {w}[/yellow]")
    elif source in ("csv", "json"):
        if not file:
            console.print("[red]--file é obrigatório para csv/json[/red]")
            raise typer.Exit(1)
        seen, new, warnings = CollectionService(repo).import_file(file, ch)
        repo.log_import(source, str(file), seen, new, warnings)
        console.print(f"[green]✔[/green] {seen} itens lidos, {new} novos.")
        for w in warnings:
            console.print(f"  [yellow]⚠ {w}[/yellow]")
    else:
        console.print("[red]--source deve ser att, csv ou json[/red]")
        raise typer.Exit(1)


@collection_app.command("inspect-att")
def collection_inspect_att(file: Optional[Path] = typer.Option(None, "--file")) -> None:
    """Mostra as tabelas existentes no seu AllTheThings.lua para ajustar o mapeamento YAML."""
    s = _settings()
    path = file or s.resolve_att_file()
    if path is None or not path.is_file():
        console.print("[red]AllTheThings.lua não encontrado.[/red] Use --file ou configure o .env.")
        raise typer.Exit(1)
    rows, warnings = ATTImporter.inspect(path)
    t = Table(title=f"Estrutura de {path.name}")
    t.add_column("Caminho"); t.add_column("Tipo"); t.add_column("Tamanho", justify="right")
    for p, typ, size in rows:
        t.add_row(p, typ, str(size))
    console.print(t)
    for w in warnings:
        console.print(f"[yellow]⚠ {w}[/yellow]")
    console.print("[dim]Copie src/wow_planner/importers/att_mapping.default.yaml, ajuste os `path` e aponte ATT_MAPPING_PATH.[/dim]")


@collection_app.command("status")
def collection_status(character: Optional[str] = typer.Option(None, "--character", help="Progresso de um personagem")) -> None:
    """Progresso geral, por categoria e por personagem."""
    s = _settings()
    conn = _conn(s)
    crepo = CharacterRepository(conn)
    ch = _character(crepo, character)
    svc = CollectionService(CollectionRepository(conn))
    overall, cats = svc.progress(ch)
    if overall.total == 0:
        console.print("Coleção vazia. Rode [cyan]wow-plan init[/cyan] e/ou [cyan]wow-plan collection import[/cyan].")
        return
    t = Table(title=f"Progresso — {overall.scope}")
    for col in ("Escopo", "Obtidos", "Em progr.", "Bloq.", "Faltam", "%", ""):
        t.add_column(col, justify="right" if col not in ("Escopo", "") else "left")
    for row in [*cats, overall]:
        t.add_row(row.scope, str(row.collected), str(row.in_progress), str(row.blocked), str(row.missing),
                  f"{row.percent:.1f}", _bar(row.percent), style="bold" if row is overall else None)
    console.print(t)
    chars = crepo.list()
    if chars and ch is None:
        t2 = Table(title="Por personagem (bloqueios automáticos por facção/classe/raça)")
        for col in ("Personagem", "Obtidos", "Bloq.", "Faltam", "%"):
            t2.add_column(col)
        for row in svc.progress_by_character(chars):
            t2.add_row(row.scope, str(row.collected), str(row.blocked), str(row.missing), f"{row.percent:.1f}")
        console.print(t2)


@collection_app.command("missing")
def collection_missing(
    category: Optional[str] = typer.Option(None, "--category"),
    faction: Optional[str] = typer.Option(None, "--faction"),
    class_name: Optional[str] = typer.Option(None, "--class"),
    source: Optional[str] = typer.Option(None, "--source"),
    expansion: Optional[str] = typer.Option(None, "--expansion"),
    difficulty: Optional[str] = typer.Option(None, "--difficulty"),
    priority: Optional[int] = typer.Option(None, "--priority", help="Prioridade máxima (1 = só as mais altas)"),
    character: Optional[str] = typer.Option(None, "--character"),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """O que falta para 100%, com filtros."""
    s = _settings()
    conn = _conn(s)
    ch = _character(CharacterRepository(conn), character)
    flt = ItemFilter(category=category, faction=normalize(faction, FACTION_ALIASES) if faction else None,
                     class_name=normalize(class_name, CLASS_ALIASES) if class_name else None, source=source,
                     expansion=expansion, difficulty=difficulty, max_priority=priority)
    rows = CollectionService(CollectionRepository(conn)).missing(flt, ch)
    t = Table(title=f"Faltando — {len(rows)} itens")
    for col in ("Prio", "Categoria", "Nome", "Fonte", "Facção", "Restrição", "Status", "Verificado"):
        t.add_column(col, overflow="fold")
    for r in rows[:limit]:
        restr = " ".join(x for x in (r["class_restriction"], r["race_restriction"]) if x) or "-"
        t.add_row(str(r["priority"]), r["category"], r["name"], r["source"] or "-", r["faction"] or "ambas", restr,
                  ITEM_STATUS_PT[ItemStatus(r["status"])], r["last_verified"] or "-")
    console.print(t)
    if len(rows) > limit:
        console.print(f"[dim]… mais {len(rows) - limit}. Use --limit.[/dim]")


@collection_app.command("set")
def collection_set(
    item: str = typer.Argument(..., help="external_id ou parte do nome"),
    status: ItemStatus = typer.Argument(...),
    character: Optional[str] = typer.Option(None, "--character"),
    progress: Optional[float] = typer.Option(None, "--progress", min=0, max=100),
) -> None:
    """Marca manualmente o status de um item (de conta ou de um personagem)."""
    s = _settings()
    conn = _conn(s)
    ch = _character(CharacterRepository(conn), character)
    names = CollectionService(CollectionRepository(conn)).set_item_status(item, status.value, ch, progress)
    if not names:
        console.print(f"[red]Nenhum item encontrado para '{item}'.[/red]")
        raise typer.Exit(1)
    console.print(f"[green]✔[/green] {len(names)} item(ns) → {ITEM_STATUS_PT[status]}: {', '.join(names[:10])}")


@collection_app.command("alerts")
def collection_alerts() -> None:
    """Itens que só certa raça/classe/facção obtém e que nenhum personagem seu cobre."""
    s = _settings()
    conn = _conn(s)
    alerts = CollectionService(CollectionRepository(conn)).restriction_alerts(CharacterRepository(conn).list())
    if not alerts:
        console.print("[green]Nenhum item restrito descoberto.[/green]")
    for a in alerts:
        console.print(f"[yellow]⚠[/yellow] {a}")


@collection_app.command("export")
def collection_export(
    format: str = typer.Option("csv", "--format", help="csv | json"),
    out: Optional[Path] = typer.Option(None, "--out"),
    character: Optional[str] = typer.Option(None, "--character"),
) -> None:
    """Exporta a coleção (com status efetivo) para CSV ou JSON."""
    s = _settings()
    conn = _conn(s)
    ch = _character(CharacterRepository(conn), character)
    out = out or Path("exports") / f"colecao_{ch.name if ch else 'conta'}_{date.today():%Y%m%d}.{format}"
    n = CollectionService(CollectionRepository(conn)).export(out, format, ch)
    console.print(f"[green]✔[/green] {n} itens exportados para {out}")


# =========================================================================== game data
@app.command("update-game-data")
def update_game_data(
    from_path: Optional[Path] = typer.Option(None, "--from", help="Pasta ou arquivo YAML/JSON com dados novos"),
    max_age: int = typer.Option(30, "--max-age", help="Dias até um registro ser considerado desatualizado"),
) -> None:
    """Valida/importa game data atualizado manualmente e lista registros a reverificar.

    Não baixa nada da internet: dados de patch notes/guias são editados em YAML/JSON e importados aqui.
    """
    s = _settings()
    conn = _conn(s)
    if from_path:
        try:
            bundle, written = game_data_service.update_from(from_path, s.data_dir)
        except (GameDataError, FileNotFoundError) as exc:
            console.print(f"[red]Atualização rejeitada:[/red] {exc}")
            raise typer.Exit(2)
        game_data_service.snapshot(conn, bundle, origin=str(from_path))
        console.print(f"[green]✔[/green] Game data v{bundle.version} validado e gravado em {s.data_dir}: {', '.join(written)}")
    bundle, stale = game_data_service.check(s.data_dir if s.data_dir.is_dir() else None, max_age)
    console.print(f"Game data ativo: v{bundle.version} · {len(bundle.races)} raças · {len(bundle.racials)} racials · "
                  f"{len(bundle.recommendations)} recomendações")
    unverified = [r.name for r in bundle.racials if r.cooldown is None and r.kind == "active"]
    if unverified:
        console.print(f"[yellow]Cooldowns não verificados ({len(unverified)}):[/yellow] {', '.join(unverified)}")
    if stale:
        console.print(f"[yellow]{len(stale)} registros com mais de {max_age} dias — reverifique:[/yellow]")
        for line in stale[:50]:
            console.print(f"  • {line}")
    else:
        console.print(f"[green]Todos os registros verificados nos últimos {max_age} dias.[/green]")


# =========================================================================== racials ao vivo
@app.command("update-racials")
def update_racials() -> None:
    """Atualiza racials: tooltips (Warcraft DB), notas oficiais (Blizzard) e notícias/tooltips (Wowhead)."""
    s = _settings()
    conn = _conn(s)
    bundle = _bundle(s)
    ensure_seeds(conn)
    console.print("Consultando Warcraft DB, fórum da Blizzard e Wowhead…")
    r = update_all(conn, bundle)
    w, b, h = r["warcraftdb"], r["blizzard"], r["wowhead"]
    console.print(("[green]✔[/green]" if not w["blocked"] else "[red]✖[/red]") +
                  f" Warcraft DB: {w['updated']} racials com mudança, {w['unchanged']} iguais, {w['new_variants']} variantes novas")
    for c in w["changes"][:30]:
        console.print(f"    [yellow]Δ[/yellow] {c['racial_id']} #{c['spell_id']} {c['field']}: "
                      f"{escape(str(c['old']))} → [bold]{escape(str(c['new']))}[/bold]")
    console.print(("[green]✔[/green]" if b["ok"] else "[red]✖[/red]") +
                  f" Blizzard: {len(b['topics'])} tópicos, {b['lines_seen']} linhas sobre racials, {b['new_notes']} novas")
    for t in b["topics"]:
        console.print(f"    • [{t['region'].upper()}] {escape(t['title'])} (último post {str(t.get('last_posted_at'))[:16]})")
    console.print(("[green]✔[/green]" if h["ok"] else "[red]✖[/red]") +
                  f" Wowhead: {h['news_seen']} notícias sobre racials ({h['new_news']} novas), {h['tooltips']} tooltips conferidos")
    for e in (w["errors"] + b["errors"] + h["errors"])[:12]:
        console.print(f"  [dim]⚠ {escape(e)}[/dim]")
    live = live_map(conn)
    notes = notes_by_racial(conn)
    pending = []
    for rid, lst in notes.items():
        v = pick_variant(live.get(rid, {}).get("variants", []), None)
        for n in lst[:3]:
            st = tooltip_status(n["text"], v)
            if st and st["status"] in ("outdated", "removed"):
                pending.append((n["posted_at"][:10], rid, n["text"]))
                break
    if pending:
        console.print("[yellow]Mudanças anunciadas que o tooltip ainda não mostra:[/yellow]")
        for d, rid, text in sorted(pending, reverse=True):
            console.print(f"    {d}  {rid}: {escape(text)}")


# =========================================================================== interface
@app.command("ui")
def ui(
    port: int = typer.Option(8765, "--port", help="Porta local"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Não abrir o navegador automaticamente"),
) -> None:
    """Abre a interface visual no navegador (roda só no seu PC)."""
    from wow_planner.web import serve
    s = _settings()
    _bundle(s)  # valida o game data antes de subir
    serve(s, port=port, open_browser=not no_browser)


if __name__ == "__main__":  # pragma: no cover
    app()
