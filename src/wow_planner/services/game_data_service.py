"""Atualização manual do banco de conhecimento + snapshot versionado no SQLite."""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from wow_planner.game_data.loader import DATA_FILES, load_game_data, stale_records
from wow_planner.models.game import GameDataBundle


def snapshot(conn: sqlite3.Connection, bundle: GameDataBundle, origin: str) -> None:
    conn.execute(
        "INSERT INTO game_data_snapshots(version, origin, payload) VALUES (?, ?, ?)",
        (bundle.version, origin, json.dumps(bundle.model_dump(), ensure_ascii=False)),
    )
    conn.commit()


def update_from(source: Path, data_dir: Path) -> tuple[GameDataBundle, list[str]]:
    """Valida os arquivos de `source` (pasta ou arquivo) e, se OK, copia para `data_dir`.

    Arquivos aceitos: races / racials / recommendations / roadmap / collection_seed (.yaml ou .json).
    Nada é gravado se a validação falhar.
    """
    candidates = [source] if source.is_file() else sorted(source.iterdir())
    accepted = {p.stem: p for p in candidates if p.suffix in (".yaml", ".yml", ".json")
                and f"{p.stem}.yaml" in DATA_FILES}
    if not accepted:
        raise FileNotFoundError(f"nenhum arquivo de game data reconhecido em {source} (esperado: {', '.join(DATA_FILES)})")

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        if data_dir.is_dir():  # parte do estado atual
            for p in data_dir.iterdir():
                shutil.copy2(p, tmpdir / p.name)
        for stem, p in accepted.items():
            for old in tmpdir.glob(f"{stem}.*"):
                old.unlink()
            shutil.copy2(p, tmpdir / (f"{stem}.json" if p.suffix == ".json" else f"{stem}.yaml"))
        bundle = load_game_data(tmpdir)  # lança GameDataError se inválido

    data_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for stem, p in accepted.items():
        for old in data_dir.glob(f"{stem}.*"):
            old.unlink()
        dest = data_dir / (f"{stem}.json" if p.suffix == ".json" else f"{stem}.yaml")
        shutil.copy2(p, dest)
        written.append(dest.name)
    return bundle, written


def check(data_dir: Path | None, max_age_days: int) -> tuple[GameDataBundle, list[str]]:
    bundle = load_game_data(data_dir)
    return bundle, stale_records(bundle, max_age_days)
