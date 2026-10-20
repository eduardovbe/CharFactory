import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# os testes conferem os textos em inglês (a língua do código), seja qual for o idioma do sistema
os.environ["CHARFACTORY_LANG"] = "en"

# Instalação do MUGEN usada como fixture (arquivos oficiais da Elecbyte: kfm, common1.cns…),
# indicada pela variável CHARFACTORY_MUGEN; sem ela, os testes que precisam do MUGEN são pulados.
_MUGEN_ENV = os.environ.get("CHARFACTORY_MUGEN") or ""


def _is_mugen(p: Path) -> bool:
    """Pasta do MUGEN com o executável (mugen.exe ou outro nome, como MUGEN-ORIGINAL.exe) e o kfm."""
    exe = (p / "mugen.exe").exists() or any("mugen" in x.name.lower() for x in p.glob("*.exe"))
    return exe and (p / "chars" / "kfm" / "kfm.def").exists()


MUGEN = Path(_MUGEN_ENV) if _MUGEN_ENV else ROOT / "mugen-nao-definido"


@pytest.fixture(scope="session")
def mugen_dir() -> Path:
    if not _is_mugen(MUGEN):
        pytest.skip(f"MUGEN com o kfm não encontrado em {MUGEN} (defina CHARFACTORY_MUGEN)")
    return MUGEN


def all_sffs():
    """Os SFF oficiais da instalação (data/, kfm e os stages/fontes de exemplo), não os chars baixados."""
    if not MUGEN.exists():
        return []
    found = {*MUGEN.glob("data/*.sff"), *(MUGEN / "chars" / "kfm").glob("*.sff"),
             *MUGEN.glob("stages/stage0*.sff"), *MUGEN.glob("stages/kfm*.sff"), *MUGEN.glob("font/*.sff")}
    return sorted(found)
