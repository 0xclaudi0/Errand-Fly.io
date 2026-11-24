import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from errand_server.config import Paths  # noqa: E402
from errand_server.db import Repository  # noqa: E402


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    p = Paths(tmp_path / "home")
    p.ensure()
    return p


@pytest.fixture
def repo(paths: Paths) -> Repository:
    r = Repository(paths.db)
    r.init_schema()
    return r
