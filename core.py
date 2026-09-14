from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", "target", ".next", ".nuxt", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", "coverage", ".idea", ".aistudio"
}
_SKIP_FOLD = {x.casefold() for x in SKIP_DIRS}
STOP_WORDS = {
    "eine","einen","einem","einer","dass","nicht","auch","oder","und","der","die","das",
    "den","dem","des","für","mit","von","auf","aus","bei","nach","über","unter","soll",
    "sollen","muss","kann","wird","werden","bitte","füge","hinzu","ändere","erstelle",
    "the","and","that","this","with","from","into","for","add","make","create","change"
}

SCHEMA = {
    "planner": {
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
                        "op": {"type": "string", "enum": ["replace", "create", "write", "delete"]},
                        "old_text": {"type": "string"},
                        "new_text": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "op", "old_text", "new_text", "content"],
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
    "planner": (
        "Du bist der PLANER eines Software-Teams. Zerlege die Aufgabe in kleine, prüfbare Schritte. "
        "Antworte NUR mit JSON: "
        '{"plan":["..."],"files":["..."],"questions":[],"acceptance":["..."]}. '
        "files enthält relevante oder wahrscheinlich zu ändernde Dateien als relative Workspace-Pfade. "
        "Fragen nur wenn wirklich blockierend."
    ),
    "coder": (
        "Du bist der CODER. Setze den Plan um. Antworte NUR mit JSON: "
        '{"edits":[EDIT,...],"notes":"..."}. '
        "EDIT hat path, op, old_text, new_text, content. "
        "Für bestehende Dateien bevorzugt op=replace: old_text muss exakt und eindeutig sein. "
        "op=create nur für neue Dateien. op=write nur für kleine vollständig gelesene Dateien. "
        "op=delete für Löschung. Keine Shell-Befehle. Keine Pfade ausserhalb des Workspace."
    ),
    "reviewer": (
        "Du bist der SENIOR REVIEWER. Prüfe Diff gegen Aufgabe und Akzeptanzkriterien. "
        'Antworte NUR mit JSON: {"verdict":"ok"|"reject","issues":["..."],"summary":"..."}. '
        "Reject nur bei echten funktionalen, Sicherheits- oder Vollständigkeitsproblemen."
    ),
}


def truncate(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + "\n...[gekürzt]"


def safe_path(workspace: pathlib.Path, rel: str, write: bool = False) -> pathlib.Path:
    if not isinstance(rel, str) or not rel.strip():
        raise ValueError("Leerer Pfad")
    relp = pathlib.Path(rel.replace("\\", "/"))
    if relp.is_absolute() or any(p in {"..", ""} for p in relp.parts):
        raise ValueError(f"Unsicherer Pfad: {rel}")
    if write and any(p.casefold() in _SKIP_FOLD for p in relp.parts):
        raise ValueError(f"Geschütztes Verzeichnis: {rel}")
    p = (workspace / relp).resolve()
    try:
        p.relative_to(workspace.resolve())
    except ValueError as exc:
        raise ValueError(f"Pfad ausserhalb Workspace: {rel}") from exc
    return p


@dataclass
class RunResult:
    task: str
    plan: dict = field(default_factory=dict)
    diff: str = ""
    final_state: dict[str, str | None] = field(default_factory=dict)
    edits: list[dict] = field(default_factory=list)
    receipt: dict = field(default_factory=dict)


class WorkspaceTransaction:
    def __init__(self, workspace: pathlib.Path, backup_root: pathlib.Path, tag: str):
        self.workspace = workspace
        self.root = backup_root / tag
        self.files: dict[str, dict] = {}

    def touch(self, rel: str):
        if rel in self.files:
            return
        p = safe_path(self.workspace, rel, write=True)
        existed = p.is_file()
        entry = {"path": rel, "existed_before": existed}
        if existed:
            data = p.read_bytes()
            entry["sha256_before"] = hashlib.sha256(data).hexdigest()
            dst = self.root / "files" / pathlib.Path(rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
        self.files[rel] = entry
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "manifest.json").write_text(
            json.dumps({"files": list(self.files.values())}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def write(self, rel: str, content: str):
        self.touch(rel)
        p = safe_path(self.workspace, rel, write=True)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".codestudio-", suffix=".tmp", dir=str(p.parent))
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(content.encode("utf-8"))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, p)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def delete(self, rel: str):
        self.touch(rel)
        p = safe_path(self.workspace, rel, write=True)
        if p.exists():
            if not p.is_file():
                raise ValueError(f"Kein Datei-Ziel: {rel}")
            p.unlink()

    def rollback(self) -> list[str]:
        restored = []
        for rel, entry in self.files.items():
            dst = safe_path(self.workspace, rel, write=True)
            if entry["existed_before"]:
                src = self.root / "files" / pathlib.Path(rel)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            elif dst.is_file():
                dst.unlink()
            restored.append(rel)
        return restored


class CodeStudioCore:
    def __init__(self, root: pathlib.Path, config: dict, log: Callable[[str], None] | None = None):
        self.root = root
        self.config = config
        self.log = log or (lambda _: None)
        w = pathlib.Path(config["workspace"])
        self.workspace = (root / w).resolve() if not w.is_absolute() else w.resolve()
        self.runs = root / "runs"
        self.backups = root / "backups"
        self.truncated: set[str] = set()
        self.fully_read: set[str] = set()

    def set_workspace(self, path: str):
        self.workspace = pathlib.Path(path).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)

    def ollama_request(self, path: str, body: dict | None = None, timeout: int | None = None) -> dict:
        url = self.config["ollama_url"].rstrip("/") + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.config.get("timeout_sec", 900)) as r:
                return json.load(r)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"Ollama HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Ollama nicht erreichbar: {url}") from exc

    def installed_models(self) -> list[str]:
        data = self.ollama_request("/api/tags", timeout=10)
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]

    def chat(self, role: str, user: str, model: str) -> dict:
        fmt: Any = SCHEMA[role] if self.config.get("structured_output", True) else "json"
        body = {
            "model": model,
            "stream": False,
            "format": fmt,
            "keep_alive": self.config.get("keep_alive", "10m"),
            "options": self.config.get("options", {}),
            "messages": [{"role": "system", "content": SYSTEM[role]}, {"role": "user", "content": user}],
        }
        raw = ""
        for attempt in (1, 2):
            raw = self.ollama_request("/api/chat", body).get("message", {}).get("content", "")
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                pass
            if attempt == 1:
                body["messages"].append({"role": "assistant", "content": raw})
                body["messages"].append({"role": "user", "content": "Nur gültiges JSON-Objekt ausgeben."})
        raise RuntimeError(f"{role} lieferte kein gültiges JSON")

    def file_tree(self) -> list[str]:
        out = []
        hard = max(200, int(self.config.get("context_max_files", 40)) * 8)
        for current, dirs, files in os.walk(self.workspace):
            dirs[:] = sorted(d for d in dirs if d.casefold() not in _SKIP_FOLD)
            cp = pathlib.Path(current)
            for name in sorted(files):
                p = cp / name
                try:
                    out.append(p.relative_to(self.workspace).as_posix())
                except ValueError:
                    pass
                if len(out) >= hard:
                    return out
        return out

    def task_keywords(self, task: str) -> list[str]:
        words = re.findall(r"[A-Za-zÄÖÜäöüß_][\w\-./]{2,}", task)
        out, seen = [], set()
        for w in words:
            k = w.strip("./-").lower()
            if len(k) >= 3 and k not in STOP_WORDS and k not in seen:
                seen.add(k)
                out.append(k)
        return out[:20]

    def scout(self, task: str, tree: list[str]) -> list[dict]:
        kws = self.task_keywords(task)
        hits = []
        max_bytes = int(self.config.get("scout_max_file_bytes", 300000))
        for rel in tree:
            score, snippet = 0, ""
            lowp = rel.lower()
            for k in kws:
                if k in lowp:
                    score += 5
            try:
                p = safe_path(self.workspace, rel)
                if p.stat().st_size > max_bytes:
                    continue
                raw = p.read_bytes()
                if b"\0" in raw[:4096]:
                    continue
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
                        a = low.rfind("\n", 0, i) + 1
                        b = low.find("\n", i)
                        snippet = txt[a:(b if b > 0 else i + 100)].strip()[:140]
            if score:
                hits.append({"path": rel, "score": score, "snippet": snippet})
        hits.sort(key=lambda h: (-h["score"], h["path"]))
        return hits[: int(self.config.get("scout_top_n", 15))]

    def read_files(self, paths: list[str]) -> dict[str, str]:
        self.truncated.clear()
        self.fully_read.clear()
        result = {}
        limit = int(self.config.get("context_max_bytes_per_file", 20000))
        max_files = int(self.config.get("context_max_files", 40))
        for rel in paths[:max_files]:
            try:
                p = safe_path(self.workspace, rel)
            except ValueError:
                continue
            if p.is_file():
                raw = p.read_bytes()
                clipped = len(raw) > limit
                use = raw[:limit] if clipped else raw
                txt = use.decode("utf-8", errors="replace")
                if clipped:
                    self.truncated.add(rel)
                    txt += "\n…[gekürzt – whole-file write verboten]"
                else:
                    self.fully_read.add(rel)
                result[rel] = txt
            else:
                result[rel] = "<neu – existiert nicht>"
        return result

    def normalize_edits(self, raw: Any) -> list[dict]:
        if not isinstance(raw, list):
            raise ValueError("edits ist keine Liste")
        out = []
        for e in raw:
            if not isinstance(e, dict):
                raise ValueError("Ungültiger Edit")
            rel = str(e.get("path", "")).strip().replace("\\", "/")
            op = str(e.get("op", "")).lower().strip()
            if op not in {"replace", "create", "write", "delete"}:
                raise ValueError(f"Ungültige Operation: {op}")
            safe_path(self.workspace, rel, write=True)
            out.append({"path": rel, "op": op, "old_text": str(e.get("old_text") or ""), "new_text": str(e.get("new_text") or ""), "content": str(e.get("content") or "")})
        if len({e["path"] for e in out}) > int(self.config.get("max_changed_files", 16)):
            raise ValueError("Zu viele geänderte Dateien")
        return out

    def current_text(self, rel: str) -> str:
        p = safe_path(self.workspace, rel)
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""

    def validate_edits(self, edits: list[dict], allowed_files: set[str]) -> tuple[dict[str, str | None], list[str]]:
        state: dict[str, str | None] = {}
        issues = []
        for e in edits:
            rel, op = e["path"], e["op"]
            if self.config.get("enforce_plan_scope", True) and rel not in allowed_files:
                issues.append(f"{rel}: nicht im Plan-Scope")
                continue
            p = safe_path(self.workspace, rel, write=True)
            if rel not in state:
                state[rel] = self.current_text(rel) if p.is_file() else None
            cur = state[rel]
            if op == "replace":
                if cur is None:
                    issues.append(f"{rel}: replace auf nicht existierende Datei")
                elif not e["old_text"]:
                    issues.append(f"{rel}: replace ohne old_text")
                else:
                    n = cur.count(e["old_text"])
                    if n == 0:
                        issues.append(f"{rel}: old_text nicht gefunden")
                    elif n > 1:
                        issues.append(f"{rel}: old_text kommt {n}x vor")
                    else:
                        state[rel] = cur.replace(e["old_text"], e["new_text"], 1)
            elif op == "create":
                if cur is not None:
                    issues.append(f"{rel}: Datei existiert bereits")
                elif not e["content"]:
                    issues.append(f"{rel}: leerer Dateiinhalt")
                else:
                    state[rel] = e["content"]
            elif op == "write":
                if cur is None:
                    issues.append(f"{rel}: write auf neue Datei – create verwenden")
                elif rel not in self.fully_read:
                    issues.append(f"{rel}: whole-file write nur nach vollständigem Lesen erlaubt")
                elif not e["content"]:
                    issues.append(f"{rel}: leerer Dateiinhalt")
                else:
                    state[rel] = e["content"]
            elif op == "delete":
                if cur is None:
                    issues.append(f"{rel}: Datei existiert nicht")
                else:
                    state[rel] = None
        final = {}
        for rel, new in state.items():
            old = self.current_text(rel) if safe_path(self.workspace, rel).is_file() else None
            if new != old:
                final[rel] = new
        if not issues and not final:
            issues.append("Keine effektive Änderung")
        return final, issues

    def diff_for_state(self, final: dict[str, str | None]) -> str:
        chunks = []
        for rel, new in final.items():
            old = self.current_text(rel)
            diff = difflib.unified_diff(old.splitlines(), ("" if new is None else new).splitlines(), fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm="")
            txt = "\n".join(diff)
            if txt:
                chunks.append(txt)
        return "\n\n".join(chunks) if chunks else "(Keine Textänderungen.)"

    def analyze(self, task: str, model: str) -> RunResult:
        tag = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        receipt = {"schema_version": 2, "tag": tag, "task": task, "workspace": str(self.workspace), "model": model, "rounds": []}
        tree = self.file_tree()
        candidates = self.scout(task, tree)
        self.log("Scout abgeschlossen")
        planner_prompt = f"AUFGABE:\n{task}\n\nDATEIBAUM:\n" + "\n".join(tree[:400])
        if candidates:
            planner_prompt += "\n\nKANDIDATEN:\n" + "\n".join(f"- {x['path']} ({x['score']}) {x['snippet']}" for x in candidates)
        self.log("Planer arbeitet …")
        plan = self.chat("planner", planner_prompt, model)
        if plan.get("questions"):
            raise RuntimeError("Planer benötigt Rückfrage: " + " | ".join(plan["questions"]))
        files = [str(x) for x in plan.get("files", [])]
        ctx = self.read_files(files)
        allowed = set(files)
        receipt.update({"plan": plan, "scout": candidates, "context_files": list(ctx)})
        feedback = ""
        max_rounds = int(self.config.get("max_review_rounds", 2))
        for rnd in range(1, max_rounds + 2):
            self.log(f"Coder Runde {rnd} …")
            prompt = f"AUFGABE:\n{task}\n\nPLAN:\n{json.dumps(plan.get('plan', []), ensure_ascii=False)}\n\nAKZEPTANZ:\n{json.dumps(plan.get('acceptance', []), ensure_ascii=False)}\n\nDATEIEN:\n{json.dumps(ctx, ensure_ascii=False)}"
            if feedback:
                prompt += "\n\nBEANSTANDUNGEN:\n" + feedback
            code = self.chat("coder", prompt, model)
            candidate = self.normalize_edits(code.get("edits", []))
            final, issues = self.validate_edits(candidate, allowed)
            if issues:
                feedback = "\n".join("- " + x for x in issues)
                receipt["rounds"].append({"round": rnd, "validation": issues})
                continue
            diff = self.diff_for_state(final)
            self.log("Reviewer arbeitet …")
            review = self.chat("reviewer", f"AUFGABE:\n{task}\n\nAKZEPTANZ:\n{json.dumps(plan.get('acceptance', []), ensure_ascii=False)}\n\nUNIFIED DIFF:\n{diff}", model)
            receipt["rounds"].append({"round": rnd, "review": review})
            if review.get("verdict") == "ok":
                receipt["status"] = "ready"
                return RunResult(task=task, plan=plan, diff=diff, final_state=final, edits=candidate, receipt=receipt)
            feedback = "\n".join("- " + str(x) for x in review.get("issues", []))
        raise RuntimeError("Reviewer hat die Änderung nicht freigegeben")

    def run_tests(self) -> dict | None:
        profiles = self.config.get("tests", [])
        if not profiles:
            return None
        max_chars = int(self.config.get("test_output_max_chars", 12000))
        for t in profiles:
            argv = t.get("argv")
            if not isinstance(argv, list) or not argv:
                continue
            name = str(t.get("name") or argv[0])
            self.log(f"Test: {name}")
            try:
                p = subprocess.run([str(x) for x in argv], cwd=self.workspace, shell=False, capture_output=True, text=True, timeout=int(t.get("timeout_sec", 600)), check=False)
                output = (p.stdout or "") + (p.stderr or "")
                if p.returncode != 0:
                    return {"name": name, "returncode": p.returncode, "output": truncate(output, max_chars)}
            except FileNotFoundError as exc:
                return {"name": name, "returncode": 127, "output": str(exc)}
            except subprocess.TimeoutExpired:
                return {"name": name, "returncode": 124, "output": "Timeout"}
        return {"name": "all", "returncode": 0, "output": ""}

    def apply(self, result: RunResult) -> dict:
        tag = result.receipt["tag"]
        tx = WorkspaceTransaction(self.workspace, self.backups, tag)
        try:
            written = []
            for rel, new in result.final_state.items():
                if new is None:
                    tx.delete(rel)
                else:
                    tx.write(rel, new)
                written.append(rel)
            result.receipt["written"] = written
            test = self.run_tests()
            result.receipt["test"] = test
            if test and test["returncode"] != 0:
                result.receipt["rollback"] = tx.rollback()
                result.receipt["status"] = "rolled-back"
            else:
                result.receipt["status"] = "applied"
            return result.receipt
        except Exception:
            if tx.files:
                result.receipt["rollback"] = tx.rollback()
            result.receipt["status"] = "error-rolled-back"
            raise
        finally:
            self.runs.mkdir(parents=True, exist_ok=True)
            (self.runs / f"{tag}.json").write_text(json.dumps(result.receipt, ensure_ascii=False, indent=2), encoding="utf-8")
