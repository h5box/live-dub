import json
import re
from pathlib import Path

import livedub

ROOT = Path(__file__).resolve().parents[1]


def version_line(path: Path) -> str:
    m = re.search(r'^version = "(.+)"', path.read_text(encoding="utf-8"), re.M)
    assert m, path
    return m[1]


def test_backend_and_app_carry_one_version():
    # the window tells an older backend still running on the port by this number: a release bumps them all
    app = ROOT / "app"
    versions = {"livedub": livedub.__version__,
                "pyproject.toml": version_line(ROOT / "pyproject.toml"),
                "package.json": json.loads((app / "package.json").read_text(encoding="utf-8"))["version"],
                "tauri.conf.json": json.loads((app / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))["version"],
                "Cargo.toml": version_line(app / "src-tauri" / "Cargo.toml")}
    assert len(set(versions.values())) == 1, versions
