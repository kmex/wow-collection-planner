"""Configuração lida de variáveis de ambiente / arquivo .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:  # python-dotenv é opcional em tempo de teste
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


def _env_path(name: str) -> Path | None:
    value = os.getenv(name, "").strip()
    return Path(value).expanduser() if value else None


@dataclass(frozen=True)
class Settings:
    db_path: Path
    wow_path: Path | None
    wow_flavor: str
    wow_account: str | None
    att_savedvariables_path: Path | None
    att_mapping_path: Path | None
    data_dir: Path

    @classmethod
    def load(cls) -> "Settings":
        if load_dotenv is not None:
            load_dotenv(override=False)
        return cls(
            db_path=_env_path("WOW_PLAN_DB") or Path("data/wow_planner.db"),
            wow_path=_env_path("WOW_PATH"),
            wow_flavor=os.getenv("WOW_FLAVOR", "_forever_").strip() or "_forever_",
            wow_account=os.getenv("WOW_ACCOUNT", "").strip() or None,
            att_savedvariables_path=_env_path("ATT_SAVEDVARIABLES_PATH"),
            att_mapping_path=_env_path("ATT_MAPPING_PATH"),
            data_dir=_env_path("WOW_PLAN_DATA_DIR") or Path("data/game_data"),
        )

    def resolve_att_file(self) -> Path | None:
        """Retorna o AllTheThings.lua configurado ou tenta descobri-lo em WTF/Account/*/SavedVariables."""
        if self.att_savedvariables_path:
            return self.att_savedvariables_path
        if not self.wow_path:
            return None
        # flavor configurado primeiro; depois os nomes conhecidos do Forever (lançamento e beta)
        flavors = list(dict.fromkeys([self.wow_flavor, "_forever_", "_classic_beta_"]))
        for flavor in flavors:
            accounts_dir = self.wow_path / flavor / "WTF" / "Account"
            if not accounts_dir.is_dir():
                continue
            accounts = ([accounts_dir / self.wow_account] if self.wow_account
                        else sorted(p for p in accounts_dir.iterdir() if p.is_dir()))
            for acc in accounts:
                candidate = acc / "SavedVariables" / "AllTheThings.lua"
                if candidate.is_file():
                    return candidate
        return None
