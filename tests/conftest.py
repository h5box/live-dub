from pathlib import Path

# Pytest creates basetemp itself, but its parent is absent on a fresh checkout.
(Path(__file__).resolve().parents[1] / ".tmp").mkdir(exist_ok=True)
