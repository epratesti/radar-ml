"""Radar ML entry — monta o bot a partir de bot_part1..4.txt"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_parts = [(_ROOT / f"bot_part{i}.txt").read_text(encoding="utf-8") for i in range(1, 5)]
_code = "".join(_parts)
exec(compile(_code, str(_ROOT / "bot_runtime.py"), "exec"), {"__name__": "__main__"})
