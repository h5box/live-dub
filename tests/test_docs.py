import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# set by the backend for each session from the start form, not written in a preset
SESSION_ONLY = {"ahead", "obs", "dub", "cookies_from_browser"}


def test_every_preset_field_the_code_reads_is_documented():
    code = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "livedub").rglob("*.py"))
    used = set(re.findall(r'\bcfg\.get\("([a-z_]+)"', code)) - SESSION_ONLY
    doc = (ROOT / "docs" / "配置.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"`([a-z_]+)`", doc))
    assert used and not used - documented, f"preset fields missing from docs/配置.md: {sorted(used - documented)}"
