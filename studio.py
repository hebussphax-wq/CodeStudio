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

SCHEMA = {
    "planer": {
        "type": "object",
        "properties": {
            "plan": {"type": "array", "items": {"type": "string"}},
            "files": {"type": "array", "items": {"type": "string"}},
            "questions": {"type": "array", "items": {"type": "string"}},
            "acceptance": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["plan", "files", "questions", "acceptance"],
    },
    "coder": {
        "type": "object",
        "properties": {
            "edits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "op": {"type": "string", "enum": ["write", "delete"]},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "op", "content"],
                },
            },
            "notes": {"type": "string"},
        },
        "required": ["edits", "notes"],
    },
    "reviewer": {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["ok", "reject"]},
            "issues": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
        },
        "required": ["verdict", "issues", "summary"],
    },
}

SYSTEM = {
    "planer": (
        "Du bist der PLANER eines Software-Teams. Du erhältst eine Aufgabe und "
        "den Dateibaum eines Projekts. Zerlege die Aufgabe in einen kleinen, "
        "prüfbaren Implementierungsplan. Antworte NUR mit JSON:\n"
        '{"plan":["Schritt 1", "..."],'
        '"files":["relativer/pfad", "..."],'
        '"questions":["..."],'
        '"acceptance":["..."]}\n'
        "files = alle Dateien, die der Coder lesen oder wahrscheinlich ändern muss. "
        "Neue Dateien dürfen genannt werden. questions nur, wenn eine fehlende "
        "Information die korrekte Umsetzung wirklich blockiert; sonst []. "
        "Keine Shell-Befehle erfinden."
    ),
    "coder": (
        "Du bist der CODER. Setze den kompletten Plan um. Antworte NUR mit JSON:\n"
        '{"edits":[{"path":"relativer/pfad","op":"write","content":"VOLLSTÄNDIGER Dateiinhalt"},'
        '{"path":"alter/pfad","op":"delete","content":""}],'
        '"notes":"kurze Erklärung"}\n'
        "Regeln:\n"
        "- immer relative Pfade;\n"
        "- bei op=write immer den vollständigen finalen Dateiinhalt liefern;\n"
        "- nie Auslassungen wie '...' oder 'rest unverändert';\n"
        "- keine Änderungen ausserhalb des Plans;\n"
        "- keine Vendor-, Build-, Cache-, Git- oder Venv-Dateien;\n"
        "- keine Shell-Befehle ausführen;\n"
        "- bei einer Review-Korrekturrunde erneut den VOLLSTÄNDIGEN Ersatz-Patch "
        "für die gesamte Aufgabe liefern, nicht nur die zuletzt korrigierte Datei."
    ),
    "reviewer": (
        "Du bist der SENIOR REVIEWER. Prüfe den vorgeschlagenen Patch gegen Aufgabe, "
        "Plan und Originaldateien. Prüfe insbesondere Korrektheit, Vollständigkeit, "
        "Imports, API-Kompatibilität, Datenverlust, Sicherheitsprobleme, Edge Cases "
        "und offensichtlich fehlende Tests. Antworte NUR mit JSON:\n"
        '{"verdict":"ok"|"reject","issues":["konkretes Problem", "..."],'
        '"summary":"kurzes Urteil"}\n'
        "reject nur bei echten funktionalen, Sicherheits- oder Vollständigkeitsproblemen; "
        "nicht wegen reiner Stilpräferenzen."
    ),
}


# ---------------------------------------------------------------------------
# Allgemein
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[:n] + "\n...[gekürzt]"


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

def ollama_request(path: str, body: dict | None = None, timeout: int | None = None) -> dict:
    url = CFG["ollama_url"].rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout or CFG.get("timeout_sec", 900)) as r:
            return json.load(r)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Ollama HTTP {exc.code} bei {path}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ollama nicht erreichbar: {url}\n{exc}") from exc


def installed_models() -> list[str]:
    data = ollama_request("/api/tags", timeout=10)
    return [m.get("name", "") for m in data.get("models", []) if m.get("name")]


def chat(role: str, user: str, extra_system: str = "", model_override: str | None = None) -> dict:
    model = model_override or CFG["roles"][role]["model"]
    system = SYSTEM[role] + ("\n\n" + extra_system if extra_system else "")

    fmt: Any = SCHEMA[role] if CFG.get("structured_output", True) else "json"
    body = {
        "model": model,
        "stream": False,
        "format": fmt,
        "keep_alive": "10m",
        "options": CFG.get("options", {}),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }

    check_prompt_budget(system, user)

    raw = ""
    for attempt in (1, 2):
        raw = ollama_request("/api/chat", body).get("message", {}).get("content", "")
        try:
            value = json.loads(raw)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
        if attempt == 1:
            log(f"{role}: ungültiges JSON – ein Wiederholungsversuch.")
            body["messages"].append({"role": "assistant", "content": raw})
            body["messages"].append({"role": "user", "content":
                "Deine Antwort war kein gültiges JSON-Objekt. Antworte erneut, NUR mit dem JSON-Objekt."})
    return {"_parse_error": True, "_raw": raw}


def check_prompt_budget(system: str, user: str) -> None:
    """Warnt, wenn der Prompt das Kontextfenster vermutlich sprengt.
    Ollama schneidet dann still vom Anfang ab – d.h. der System-Prompt geht verloren."""
    num_ctx = int(CFG.get("options", {}).get("num_ctx", 2048))
    chars_per_token = float(CFG.get("chars_per_token", 3.5))
    est = int(len(system + user) / chars_per_token)
    if est > num_ctx * 0.85:
        log(f"WARNUNG: Prompt ~{est} Tokens, num_ctx={num_ctx}. "
            "Kontext wird abgeschnitten – Ergebnis unzuverlässig. "
            "Weniger Dateien (context_max_files) oder num_ctx erhöhen.")


# ---------------------------------------------------------------------------
# Workspace / Pfade
# ---------------------------------------------------------------------------

def safe_path(rel: str, *, for_write: bool = False) -> pathlib.Path:
    if not isinstance(rel, str) or not rel.strip():
        raise ValueError("Leerer Dateipfad.")

    rel_path = pathlib.Path(rel.replace("\\", "/"))
    if rel_path.is_absolute():
        raise ValueError(f"Absoluter Pfad nicht erlaubt: {rel}")

    if any(part in {"..", ""} for part in rel_path.parts):
        raise ValueError(f"Unsicherer relativer Pfad: {rel}")

    if for_write and set(rel_path.parts) & WRITE_BLOCK_DIRS:
        raise ValueError(f"Geschütztes Verzeichnis darf nicht verändert werden: {rel}")

    p = (WS / rel_path).resolve()
    try:
        p.relative_to(WS)
    except ValueError as exc:
        raise ValueError(f"Pfad ausserhalb des Workspace: {rel}") from exc

    return p


def file_tree() -> list[str]:
    out: list[str] = []
    if not WS.exists():
        return out

    hard_limit = max(200, int(CFG.get("context_max_files", 40)) * 8)

    for current, dirs, files in os.walk(WS):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        current_path = pathlib.Path(current)
        for name in sorted(files):
            p = current_path / name
            try:
                rel = p.relative_to(WS).as_posix()
            except ValueError:
                continue
            out.append(rel)
            if len(out) >= hard_limit:
                return out
    return out


def read_files(paths: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    limit = int(CFG.get("context_max_bytes_per_file", 16000))
    max_files = int(CFG.get("context_max_files", 40))

    num_ctx = int(CFG.get("options", {}).get("num_ctx", 2048))
    budget = int(num_ctx * float(CFG.get("chars_per_token", 3.5)) * 0.5)  # halbes Fenster für Dateien
    used = 0

    seen: set[str] = set()
    for rel in paths:
        if len(result) >= max_files:
            break
        if used >= budget:
            log(f"Kontextbudget erreicht ({budget} Zeichen) – weitere Dateien ausgelassen: {rel}")
            continue
        if not isinstance(rel, str) or rel in seen:
            continue
        seen.add(rel)

        try:
            p = safe_path(rel)
        except ValueError:
            continue

        if p.is_file():
            try:
                raw = p.read_bytes()
            except OSError as exc:
                result[rel] = f"<Lesefehler: {exc}>"
                continue

            raw = raw[: limit + 1]
            clipped = len(raw) > limit
            if clipped:
                raw = raw[:limit]

            txt = raw.decode("utf-8", errors="replace")
            result[rel] = txt + ("\n…[gekürzt]" if clipped else "")
            used += len(txt)
        else:
            result[rel] = "<neu – existiert noch nicht>"

    return result


# ---------------------------------------------------------------------------
# Scout: Kandidatendateien per Stichwort- und Importsuche
# ---------------------------------------------------------------------------

import re

_STOP = {"eine","einen","einem","einer","dass","nicht","auch","oder","und","der","die","das",
         "den","dem","des","für","mit","von","auf","aus","bei","nach","über","unter","soll",
         "sollen","muss","kann","wird","werden","bitte","füge","hinzu","ändere","erstelle",
         "the","and","that","this","with","from","into","for","add","make","create","change"}

def task_keywords(task: str) -> list[str]:
    words = re.findall(r"[A-Za-zÄÖÜäöüß_][\w\-./]{2,}", task)
    out, seen = [], set()
    for w in words:
        k = w.strip("./-").lower()
        if len(k) >= 3 and k not in _STOP and k not in seen:
            seen.add(k); out.append(k)
    return out[:20]


def scout(task: str, tree: list[str]) -> list[dict]:
    """Rangiert Workspace-Dateien nach Treffern von Aufgaben-Stichwörtern
    in Pfad und Inhalt. Rein deterministisch. Gibt Top-N mit Snippet zurück."""
    kws = task_keywords(task)
    if not kws:
        return []
    max_bytes = int(CFG.get("scout_max_file_bytes", 200000))
    top_n = int(CFG.get("scout_top_n", 15))
    hits: list[dict] = []
    for rel in tree:
        score, snippet = 0, ""
        low_path = rel.lower()
        for k in kws:
            if k in low_path:
                score += 5
        try:
            p = safe_path(rel)
            if p.stat().st_size > max_bytes:
                continue
            raw = p.read_bytes()
            if b"\0" in raw[:4096]:
                continue  # binär
            txt = raw.decode("utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        low = txt.lower()
        for k in kws:
            c = low.count(k)
            if c:
                score += min(c, 10)
                if not snippet:
                    i = low.find(k)
                    line_start = low.rfind("\n", 0, i) + 1
                    line_end = low.find("\n", i)
                    snippet = txt[line_start:(line_end if line_end > 0 else i + 80)].strip()[:120]
        if score:
            hits.append({"path": rel, "score": score, "snippet": snippet})
    hits.sort(key=lambda h: (-h["score"], h["path"]))
    return hits[:top_n]


_IMPORT_RES = [
    re.compile(r"^\s*from\s+([\w.]+)\s+import", re.M),       # Python
    re.compile(r"^\s*import\s+([\w.]+)", re.M),                # Python
    re.compile(r"""(?:from|require\()\s*['"](\.{1,2}/[^'"]+)['"]""", re.M),  # JS/TS relativ
]

def resolve_imports(rel: str, tree: list[str]) -> list[str]:
    """Direkte Importe einer Datei auf Workspace-Dateien abbilden (Python-Module, JS-relativ)."""
    try:
        txt = safe_path(rel).read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return []
    tree_set = set(tree)
    found: list[str] = []
    base = pathlib.PurePosixPath(rel).parent
    for rx in _IMPORT_RES:
        for m in rx.findall(txt):
            if m.startswith("."):
                cand = pathlib.PurePosixPath((base / m).as_posix().replace("/./", "/"))
                for ext in ("", ".js", ".ts", ".jsx", ".tsx", ".mjs", "/index.js", "/index.ts"):
                    c = (cand.as_posix() + ext).lstrip("./")
                    if c in tree_set:
                        found.append(c); break
            else:
                mod = m.replace(".", "/")
                for c in (mod + ".py", mod + "/__init__.py"):
                    if c in tree_set:
                        found.append(c); break
    return [f for f in dict.fromkeys(found) if f != rel]


def expand_with_imports(files: list[str], tree: list[str]) -> list[str]:
    out = list(dict.fromkeys(f for f in files if isinstance(f, str)))
    limit = int(CFG.get("context_max_files", 40))
    for f in list(out):
        for dep in resolve_imports(f, tree):
            if dep not in out and len(out) < limit:
                out.append(dep)
    return out


# ---------------------------------------------------------------------------
# Patch-Normalisierung und Diff
# ---------------------------------------------------------------------------

def normalize_edits(raw_edits: Any) -> list[dict]:
    if not isinstance(raw_edits, list):
        raise ValueError("edits ist keine Liste.")

    max_files = int(CFG.get("max_changed_files", 12))
    max_bytes = int(CFG.get("max_generated_bytes_per_file", 500000))

    normalized: list[dict] = []
    seen: set[str] = set()

    for raw in raw_edits:
        if not isinstance(raw, dict):
            raise ValueError("Ungültiger Edit-Eintrag.")

        rel = str(raw.get("path", "")).strip().replace("\\", "/")
        op = str(raw.get("op", "write")).strip().lower()
        content = raw.get("content", "")

        if not rel:
            raise ValueError("Edit ohne path.")
        if rel in seen:
            raise ValueError(f"Doppelter Edit-Pfad: {rel}")
        seen.add(rel)

        safe_path(rel, for_write=True)

        if op not in {"write", "delete"}:
            raise ValueError(f"Unbekannte Operation {op!r} für {rel}")

        if op == "write":
            if not isinstance(content, str):
                raise ValueError(f"content muss String sein: {rel}")
            if len(content.encode("utf-8")) > max_bytes:
                raise ValueError(f"Generierte Datei zu gross: {rel}")
        else:
            content = ""

        normalized.append({"path": rel, "op": op, "content": content})

    if len(normalized) > max_files:
        raise ValueError(
            f"Zu viele geänderte Dateien ({len(normalized)} > {max_files}). "
            "Aufgabe kleiner schneiden oder Limit bewusst erhöhen."
        )

    return normalized


def current_text(rel: str) -> str:
    p = safe_path(rel)
    if not p.is_file():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def diff_for_edits(edits: list[dict]) -> str:
    chunks: list[str] = []

    for e in edits:
        rel = e["path"]
        old = current_text(rel)
        new = "" if e["op"] == "delete" else e["content"]

        diff = difflib.unified_diff(
            old.splitlines(),
            new.splitlines(),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
            lineterm="",
        )
        text = "\n".join(diff)
        if text:
            chunks.append(text)

    return "\n\n".join(chunks) if chunks else "(Keine Textänderungen.)"


# ---------------------------------------------------------------------------
# Checkpoint / Backup / Anwenden
# ---------------------------------------------------------------------------

def backup_affected_files(tag: str, edits: list[dict]) -> str:
    backup_dir = BACKUPS / tag
    files_dir = backup_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "tag": tag,
        "workspace": str(WS),
        "files": [],
    }

    for e in edits:
        rel = e["path"]
        src = safe_path(rel)
        existed = src.is_file()

        manifest["files"].append({
            "path": rel,
            "existed_before": existed,
            "op": e["op"],
        })

        if existed:
            dst = files_dir / pathlib.Path(rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    (backup_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(backup_dir)


def restore_backup(backup_dir: str) -> list[str]:
    """Stellt die im Manifest erfassten Dateien aus dem Backup wieder her."""
    bdir = pathlib.Path(backup_dir)
    manifest = json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))
    restored: list[str] = []
    for f in manifest["files"]:
        dst = safe_path(f["path"], for_write=True)
        if f["existed_before"]:
            src = bdir / "files" / pathlib.Path(f["path"])
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        elif dst.is_file():
            dst.unlink()
        restored.append(f["path"])
    return restored


def git_branch_for_task(tag: str) -> dict:
    result = {"attempted": False, "ok": False, "branch": "", "detail": ""}
    if not CFG.get("git_branch_per_task", False) or not (WS / ".git").exists():
        result["detail"] = "deaktiviert oder kein Git-Repo"
        return result
    result["attempted"] = True
    branch = f"studio/{tag}"
    proc = subprocess.run(["git", "-C", str(WS), "checkout", "-b", branch],
                          capture_output=True, text=True, check=False)
    result["ok"] = proc.returncode == 0
    result["branch"] = branch if result["ok"] else ""
    result["detail"] = truncate((proc.stdout + proc.stderr).strip(), 500)
    return result


def git_commit_result(tag: str, task: str) -> dict:
    """Commit der KI-Änderung NACH erfolgreichem Test (nur wenn Branch-Modus aktiv)."""
    result = {"attempted": False, "ok": False, "detail": ""}
    if not CFG.get("git_branch_per_task", False) or not (WS / ".git").exists():
        return result
    result["attempted"] = True
    subprocess.run(["git", "-C", str(WS), "add", "-A"], capture_output=True, check=False)
    proc = subprocess.run(["git", "-C", str(WS), "commit", "--no-verify", "-m",
                           f"studio {tag}: {task[:72]}"], capture_output=True, text=True, check=False)
    result["ok"] = proc.returncode == 0
    result["detail"] = truncate((proc.stdout + proc.stderr).strip(), 500)
    return result


def git_checkpoint(tag: str) -> dict:
    result = {"attempted": False, "ok": False, "detail": ""}

    if not CFG.get("git_checkpoint", True):
        result["detail"] = "deaktiviert"
        return result

    if not (WS / ".git").exists():
        result["detail"] = "kein Git-Repo"
        return result

    result["attempted"] = True

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(WS), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    name = git("config", "user.name")
    email = git("config", "user.email")
    if name.returncode != 0 or email.returncode != 0 or not name.stdout.strip() or not email.stdout.strip():
        result["detail"] = "Git-Identität fehlt; Dateibackup bleibt aktiv."
        return result

    status = git("status", "--porcelain")
    if status.returncode != 0:
        result["detail"] = truncate(status.stderr.strip(), 1000)
        return result

    if not status.stdout.strip():
        head = git("rev-parse", "--short", "HEAD")
        result["ok"] = head.returncode == 0
        result["detail"] = (
            f"Arbeitsbaum sauber; HEAD {head.stdout.strip()}"
            if result["ok"]
            else "Arbeitsbaum sauber; noch kein HEAD."
        )
        return result

    add = git("add", "-A")
    if add.returncode != 0:
        result["detail"] = truncate(add.stderr.strip(), 1000)
        return result

    commit = git("commit", "--no-verify", "-m", f"studio checkpoint {tag}")
    if commit.returncode != 0:
        result["detail"] = (
            "Git-Commit fehlgeschlagen; Dateibackup bleibt aktiv. "
            + truncate((commit.stdout + commit.stderr).strip(), 1500)
        )
        return result

    head = git("rev-parse", "--short", "HEAD")
    result["ok"] = head.returncode == 0
    result["detail"] = (
        f"Commit {head.stdout.strip()}"
        if result["ok"]
        else "Commit erstellt."
    )
    return result


def apply_edits(edits: list[dict]) -> list[str]:
    changed: list[str] = []

    for e in edits:
        p = safe_path(e["path"], for_write=True)

        if e["op"] == "delete":
            if p.exists():
                if not p.is_file():
                    raise ValueError(f"Delete-Ziel ist keine Datei: {e['path']}")
                p.unlink()
                changed.append(e["path"])
            continue

        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(e["content"], encoding="utf-8", newline="\n")
        changed.append(e["path"])

    return changed


def run_tests() -> dict | None:
    cmd = str(CFG.get("test_command", "")).strip()
    if not cmd:
        return None

    log(f"Tests: {cmd}")
    proc = subprocess.run(
        cmd,
        shell=True,
        cwd=WS,
        capture_output=True,
        text=True,
        check=False,
    )

    max_chars = int(CFG.get("test_output_max_chars", 12000))
    output = truncate((proc.stdout or "") + (proc.stderr or ""), max_chars)

    return {
        "command": cmd,
        "returncode": proc.returncode,
        "output": output,
    }


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def collect_plan(task: str, tree: list[str], auto_yes: bool, model_override: str | None,
                 candidates: list[dict] | None = None) -> dict:
    answers: list[dict[str, str]] = []
    scout_block = ""
    if candidates:
        scout_block = "\n\nKANDIDATEN (Stichworttreffer, absteigend):\n" + "\n".join(
            f"- {c['path']}  ({c['score']})  {c['snippet']}" for c in candidates)
    max_rounds = int(CFG.get("max_question_rounds", 2))

    for question_round in range(max_rounds + 1):
        suffix = ""
        if answers:
            suffix = "\n\nBEREITS BEANTWORTETE RÜCKFRAGEN:\n" + json_dumps(answers)

        plan = chat(
            "planer",
            f"AUFGABE:\n{task}\n\nDATEIBAUM:\n" + "\n".join(tree)
            + scout_block + suffix,
            model_override=model_override,
        )

        if plan.get("_parse_error"):
            raise RuntimeError("Planer lieferte kein gültiges JSON.")

        questions = plan.get("questions", [])
        if not questions:
            return plan

        if not isinstance(questions, list):
            raise RuntimeError("Planer-JSON: questions ist keine Liste.")

        if auto_yes:
            raise RuntimeError(
                "Planer hat Rückfragen, aber --yes erlaubt keine sichere Interaktion:\n- "
                + "\n- ".join(str(q) for q in questions)
            )

        if question_round >= max_rounds:
            raise RuntimeError("Maximale Rückfrage-Runden erreicht.")

        print("\nRückfragen des Planers:")
        for q in questions:
            answer = input(f"- {q}\n  Antwort: ").strip()
            answers.append({"question": str(q), "answer": answer})

    raise RuntimeError("Planung konnte nicht abgeschlossen werden.")


def run(task: str, auto_yes: bool, dry_run: bool, model_override: str | None = None) -> dict:
    tag = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    receipt: dict[str, Any] = {
        "tag": tag,
        "task": task,
        "workspace": str(WS),
        "model_override": model_override,
        "rounds": [],
    }

    try:
        tree = file_tree()

        # 0) Scout
        candidates = scout(task, tree)
        receipt["scout"] = candidates
        if candidates:
            log("Scout: " + ", ".join(c["path"] for c in candidates[:8]))

        # 1) Planer
        log("Planer …")
        plan = collect_plan(task, tree, auto_yes, model_override, candidates)
        receipt["plan"] = plan

        print("\nPLAN:")
        for i, step in enumerate(plan.get("plan", []), 1):
            print(f" {i}. {step}")

        if plan.get("acceptance"):
            print("\nAKZEPTANZ:")
            for item in plan["acceptance"]:
                print(" -", item)

        files = expand_with_imports(plan.get("files", []), tree)
        ctx = read_files(files)
        receipt["context_files"] = list(ctx.keys())

        # 2) Coder -> Reviewer -> Korrekturrunden
        candidate = coder_reviewer_loop(task, plan, ctx, tree, "", model_override, receipt)

        # 3) Diff
        diff_text = diff_for_edits(candidate)
        receipt["diff"] = diff_text
        show_diff(diff_text)

        if dry_run:
            receipt["written"] = []
            receipt["status"] = "dry-run"
            log("Dry-run: nichts angewendet.")
            return receipt

        # 4) Anwenden
        print("\nÄNDERUNGEN:")
        for e in candidate:
            print(f" - {e['op']:6} {e['path']}")

        if not auto_yes:
            answer = input("\nAnwenden? [j/N] ").strip().lower()
            if answer not in {"j", "ja", "y", "yes"}:
                receipt["written"] = []
                receipt["status"] = "verworfen"
                log("Verworfen.")
                return receipt

        receipt["git_branch"] = git_branch_for_task(tag)
        receipt["git_checkpoint"] = git_checkpoint(tag)
        # Backup ist immer aktiv, unabhängig von Git. Erstes Backup = Rollback-Punkt.
        receipt["backup"] = backup_affected_files(tag, candidate)
        receipt["written"] = apply_edits(candidate)
        log(f"Angewendet: {receipt['written']}")

        # 5) Tests + Reparaturschleife
        receipt["test"] = run_tests()
        max_repair = int(CFG.get("max_repair_rounds", 1))
        repair = 0
        while receipt["test"] is not None and receipt["test"]["returncode"] != 0 and repair < max_repair:
            repair += 1
            log(f"Tests fehlgeschlagen (rc={receipt['test']['returncode']}) – Reparaturrunde {repair}/{max_repair}")
            print(receipt["test"]["output"])
            touched = expand_with_imports(
                list(dict.fromkeys([e["path"] for e in candidate] + plan.get("files", []))), tree)
            ctx_now = read_files(touched)
            fb = ("TESTFEHLER – der Patch wurde angewendet, die Tests schlagen fehl. "
                  "Die ORIGINALDATEIEN unten sind der AKTUELLE Stand nach deinem Patch. "
                  "Behebe die Ursache und liefere den vollständigen Ersatz-Patch.\n"
                  f"Befehl: {receipt['test']['command']}\nAusgabe:\n{receipt['test']['output']}")
            try:
                candidate = coder_reviewer_loop(task, plan, ctx_now, tree, fb, model_override, receipt,
                                                tag_prefix=f"repair{repair}")
            except RuntimeError as exc:
                log(f"Reparatur abgebrochen: {exc}")
                break
            diff_text = diff_for_edits(candidate)
            receipt[f"repair{repair}_diff"] = diff_text
            show_diff(diff_text)
            if not auto_yes and input("\nReparatur anwenden? [j/N] ").strip().lower() not in {"j","ja","y","yes"}:
                log("Reparatur verworfen.")
                break
            backup_affected_files(f"{tag}-repair{repair}", candidate)
            receipt["written"] = list(dict.fromkeys(receipt["written"] + apply_edits(candidate)))
            receipt["test"] = run_tests()

        if receipt["test"] is not None:
            rc = receipt["test"]["returncode"]
            log(f"Test return code: {rc}")
            if receipt["test"]["output"]:
                print("\nTESTAUSGABE:")
                print(receipt["test"]["output"])
            if rc != 0:
                if CFG.get("rollback_on_test_failure", True):
                    receipt["rollback"] = restore_backup(receipt["backup"])
                    receipt["status"] = "rolled-back"
                    log(f"Tests endgültig fehlgeschlagen – Rollback aus Backup: {receipt['rollback']}")
                    log(f"Patch bleibt im Receipt erhalten: runs/{tag}.json")
                    return receipt
                receipt["status"] = "applied-tests-failed"
                log("Tests fehlgeschlagen; Änderungen bleiben (rollback_on_test_failure=false).")
                return receipt

        receipt["git_commit"] = git_commit_result(tag, task)
        receipt["status"] = "applied"
        return receipt

    except Exception as exc:
        receipt["status"] = "error"
        receipt["error"] = str(exc)
        log(f"FEHLER: {exc}")
        return receipt

    finally:
        RUNS.mkdir(parents=True, exist_ok=True)
        path = RUNS / f"{tag}.json"
        path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"Receipt: {path.relative_to(ROOT).as_posix()}")


def show_diff(diff_text: str) -> None:
    print("\n" + "=" * 80)
    print("DIFF")
    print("=" * 80)
    print(diff_text)
    print("=" * 80)


def coder_reviewer_loop(task: str, plan: dict, ctx: dict, tree: list[str], feedback: str,
                        model_override: str | None, receipt: dict, tag_prefix: str = "") -> list[dict]:
    """Coder -> Reviewer, max. N Korrekturrunden. Gibt freigegebenen Patch zurück oder wirft."""
    candidate: list[dict] = []
    approved = False
    max_review_rounds = int(CFG.get("max_review_rounds", 2))
    if True:
        for rnd in range(1, max_review_rounds + 2):
            log(f"Coder (Runde {rnd}) …")

            prompt = (
                f"AUFGABE:\n{task}\n\n"
                f"PLAN:\n{json_dumps(plan.get('plan', []))}\n\n"
                f"AKZEPTANZ:\n{json_dumps(plan.get('acceptance', []))}\n\n"
                f"DATEIBAUM:\n" + "\n".join(tree[:200]) + "\n\n"
                f"ORIGINALDATEIEN:\n{json_dumps(ctx)}"
            )

            if candidate:
                prompt += (
                    "\n\nLETZTER VOLLSTÄNDIGER PATCH:\n"
                    + json_dumps(candidate)
                    + "\n\nWICHTIG: Liefere jetzt wieder den vollständigen Ersatz-Patch "
                      "für die gesamte Aufgabe."
                )

            if feedback:
                prompt += "\n\nREVIEW-BEANSTANDUNGEN:\n" + feedback

            code = chat("coder", prompt, model_override=model_override)
            if code.get("_parse_error"):
                raise RuntimeError("Coder lieferte kein gültiges JSON.")

            candidate = normalize_edits(code.get("edits", []))
            if not candidate:
                raise RuntimeError("Coder lieferte keine Änderungen.")

            log("Reviewer …")
            review = chat(
                "reviewer",
                (
                    f"AUFGABE:\n{task}\n\n"
                    f"PLAN:\n{json_dumps(plan.get('plan', []))}\n\n"
                    f"AKZEPTANZ:\n{json_dumps(plan.get('acceptance', []))}\n\n"
                    f"ORIGINALDATEIEN:\n{json_dumps(ctx)}\n\n"
                    f"VOLLSTÄNDIGER PATCH:\n{json_dumps(candidate)}"
                ),
                model_override=model_override,
            )

            if review.get("_parse_error"):
                raise RuntimeError("Reviewer lieferte kein gültiges JSON.")

            receipt["rounds"].append({
                "phase": tag_prefix or "initial",
                "round": rnd,
                "coder_notes": code.get("notes", ""),
                "review": review,
                "files": [e["path"] for e in candidate],
            })

            verdict = str(review.get("verdict", "")).lower()
            if verdict == "ok":
                approved = True
                log("Review: OK")
                break

            issues = review.get("issues", [])
            if not isinstance(issues, list):
                issues = [str(issues)]

            feedback = "\n".join(f"- {item}" for item in issues if str(item).strip())
            if not feedback:
                feedback = "- Reviewer hat abgelehnt, aber keine konkrete Beanstandung geliefert."

            log("Review: REJECT")
            print(feedback)

        if not approved:
            raise RuntimeError(
                "Reviewer hat den Patch nach den erlaubten Korrekturrunden nicht freigegeben. "
                "Es wird nichts angewendet."
            )

        receipt[(tag_prefix + "_" if tag_prefix else "") + "approved_edits"] = [
            {"path": e["path"], "op": e["op"], "bytes": len(e["content"].encode("utf-8"))}
            for e in candidate
        ]
        return candidate


# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------

def model_installed(wanted: str, models: list[str]) -> bool:
    wanted = wanted.strip().lower()
    names = [m.strip().lower() for m in models]
    if wanted in names:
        return True
    return any(
        m == wanted
        or m.startswith(wanted + "-")
        or m.startswith(wanted + ":")
        or wanted.startswith(m + "-")
        or wanted.startswith(m + ":")
        for m in names
    )


def doctor() -> int:
    print(f"=== KI-Codestudio Doctor v{VERSION} ===")
    print(f"Python:    {sys.version.split()[0]}")
    print(f"Workspace: {WS}")
    print(f"Ollama:    {CFG['ollama_url']}")

    try:
        models = installed_models()
        print("Ollama API: OK")
        print("Modelle:")
        if models:
            for m in models:
                print(" -", m)
        else:
            print(" - keine")
    except Exception as exc:
        print("Ollama API: FEHLER")
        print(exc)
        return 1

    nvidia = shutil.which("nvidia-smi")
    if nvidia:
        proc = subprocess.run(
            [
                nvidia,
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        print("NVIDIA:")
        print(proc.stdout.strip() or proc.stderr.strip() or "keine Ausgabe")
    else:
        print("NVIDIA: nvidia-smi nicht im PATH gefunden.")

    profiles = CFG.get("profiles") or {
        "8":  {"model": "qwen2.5-coder:7b", "num_ctx": 8192},
        "12": {"model": "qwen2.5-coder:14b", "num_ctx": 12288},
        "16": {"model": "gpt-oss:20b", "num_ctx": 16384},
        "24": {"model": "qwen3-coder:30b", "num_ctx": 16384},
    }
    print("\nEmpfohlene Profile:")
    for gb in ("8", "12", "16", "24"):
        prof = profiles.get(gb) or profiles.get(int(gb))
        if not prof:
            continue
        tag = prof.get("model", "")
        mark = "  (installiert)" if model_installed(tag, models) else ""
        print(f" - {gb:>2} GB  {tag}  num_ctx={prof.get('num_ctx', '')}{mark}")

    required = {
        CFG["roles"]["planer"]["model"],
        CFG["roles"]["coder"]["model"],
        CFG["roles"]["reviewer"]["model"],
    }
    missing = [m for m in sorted(required) if not model_installed(m, models)]
    if missing:
        print("\nFehlende konfigurierte Modelle:")
        for m in missing:
            print(f" - {m}   -> ollama pull {m}")
        return 2

    print("\nKonfiguration: OK")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Lokales KI-Codestudio mit Ollama")
    ap.add_argument("task", nargs="?", help="Entwicklungsauftrag")
    ap.add_argument("--yes", action="store_true", help="Patch nach Review ohne Rückfrage anwenden")
    ap.add_argument("--dry-run", action="store_true", help="Diff erzeugen, aber nichts schreiben")
    ap.add_argument("--doctor", action="store_true", help="Ollama/NVIDIA/Modelle prüfen")
    ap.add_argument("--model", help="Modell für alle Rollen nur in diesem Lauf überschreiben")
    args = ap.parse_args()

    WS.mkdir(parents=True, exist_ok=True)

    if args.doctor:
        return doctor()

    if args.task:
        run(args.task, args.yes, args.dry_run, args.model)
        return 0

    print(f"KI-Codestudio v{VERSION} – Workspace: {WS}")
    print("Leer eingeben = beenden.")
    while True:
        try:
            task = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not task:
            break

        run(task, args.yes, args.dry_run, args.model)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())