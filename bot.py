# -*- coding: utf-8 -*-
"""Loader: monta bot.py a partir de bot_part1..4.txt (evita limite de push)."""
from pathlib import Path
_code = "".join(
    Path(__file__).with_name(f"bot_part{i}.txt").read_text(encoding="utf-8")
    for i in range(1, 5)
)
exec(compile(_code, str(Path(__file__).resolve()), "exec"), globals())
