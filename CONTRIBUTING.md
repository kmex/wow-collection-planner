# Como contribuir

Obrigado por ajudar! Dados do jogo mudam a cada patch, então correções de dados são a contribuição mais valiosa.

## Corrigir ou atualizar dados do jogo
Os dados ficam em `src/wow_planner/game_data/data/`:

| Arquivo | Conteúdo |
|---|---|
| `races.yaml` | raças, classes permitidas, notas gerais |
| `racials.yaml` | racials (efeito resumido em PT-BR) |
| `recommendations.yaml` | ordem de raças por classe/facção/objetivo |
| `roadmap.yaml` | etapas do plano |
| `professions.yaml` | profissões, interesses, duplas, buffs de acampamento |
| `collection_seed.yaml` | colecionáveis-semente |

Regras:
1. **Todo registro precisa de `source` (link) e `last_verified` (data).** Os testes falham sem isso.
2. Não invente valores. Se não tiver fonte, marque `confidence: unverified`.
3. Atualize o `version` do arquivo e anote no `CHANGELOG.md`.

## Rodar localmente
```bat
setup.bat
.venv\Scripts\python -m pytest -q
```
Linux/macOS: `python -m venv .venv && .venv/bin/pip install -e .[dev] && .venv/bin/pytest -q`.

## Pull requests
- Um assunto por PR, com os testes passando (`pytest -q`).
- Textos de interface em português; tooltips oficiais ficam em inglês.

## Publicar uma nova versão (mantenedor)
1. Suba o `version` em `pyproject.toml` e adicione a seção no `CHANGELOG.md`.
2. Rode `publicar-github.bat`, ou faça manualmente: `git commit`, depois `git tag vX.Y.Z` e `git push --tags`.
3. O workflow **Release** roda os testes, gera o `.zip` e o `.whl` e cria a página de download em *Releases*.
