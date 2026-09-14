#!/usr/bin/env python3
"""KI-Codestudio v0.3 – lokale Rollen-Orchestrierung über Ollama.

Pipeline:
Eingabe -> SCOUT -> PLANER -> CODER -> REVIEWER -> Diff -> Freigabe -> Anwenden
        -> Tests -> (bei Fehlschlag: Reparaturrunde CODER -> REVIEWER -> Anwenden -> Tests)
        -> bei endgültigem Fehlschlag: Rollback aus Backup

Scout = deterministische Stichwort- und Importsuche (grep), kein Embedding-Index.
Tester = dein test_command, kein LLM. Nur die Reparatur ist LLM-gestützt.

Nur Python-Stdlib. Keine KI-generierten Shell-Befehle werden ausgeführt.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any

VERSION = "0.3.0"

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parent
CFG_PATH = ROOT / "config.json"
CFG = json.loads(CFG_PATH.read_text(encoding="utf-8-sig"))

_workspace_cfg = pathlib.Path(CFG["workspace"])
WS = (
    (ROOT / _workspace_cfg).resolve()
    if not _workspace_cfg.is_absolute()
    else _workspace_cfg.resolve()
)

RUNS = ROOT / "runs"
BACKUPS = ROOT / "backups"

SKIP_DIRS = {
    ".git", ".hg", ".svn",
    "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", "target", ".next", ".nuxt",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    "coverage", ".idea"
}

WRITE_BLOCK_DIRS = SKIP_DIRS | {".aistudio"}
