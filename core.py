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
import uuid
import copy
from processrunner import run_command, ProcessTreeUncertain
from safety import (safe_path, WorkspaceTransaction, identity, digest, canonical,
                    read_text, ensure_source_text, atomic_bytes, relative, redact)
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


@dataclass
class RunResult:
    task: str
    plan: dict = field(default_factory=dict)
    diff: str = ""
    final_state: dict[str, str | None] = field(default_factory=dict)
    edits: list[dict] = field(default_factory=list)
    receipt: dict = field(default_factory=dict)
    binding: dict = field(default_factory=dict)


class CodeStudioCore:
    def __init__(self, root: pathlib.Path, config: dict, log: Callable[[str], None] | None = None, transport=None):
        self.root = root
        self.config = copy.deepcopy(config)
        self.log = log or (lambda _: None)
        self.transport = transport
        w = pathlib.Path(config["workspace"])
        self.workspace = (root / w).resolve() if not w.is_absolute() else w.resolve()
        state_root = pathlib.Path(config.get("state_dir", root))
        self.runs = state_root / "runs"
        self.backups = state_root / "backups"
        self.read_identity = {}
        self.pending = {}
        self._busy = False
        self.truncated: set[str] = set()
        self.fully_read: set[str] = set()

    def set_workspace(self, path: str):
        if self._busy:
            raise RuntimeError("Workspace ist während einer Analyse gesperrt")
        target = pathlib.Path(path).resolve(strict=True)
        if not target.is_dir():
            raise ValueError("Workspace muss ein vorhandener Ordner sein")
        self.workspace = target
        self.fully_read.clear()
        self.read_identity.clear()
        self.pending.clear()

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
        if self.transport is not None:
            return self.transport('models', {})['models']
        data = self.ollama_request("/api/tags", timeout=10)
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]

    def chat(self, role: str, user: str, model: str) -> dict:
        if self.transport is not None:
            result = self.transport('chat', {'role': role, 'model': model, 'system': SYSTEM[role],
                'messages': [{'role': 'user', 'content': user}], 'schema': SCHEMA[role]})
            if not isinstance(result, dict):
                raise ValueError('Host lieferte kein strukturiertes Modellobjekt.')
            return result
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
            dirs[:] = sorted(d for d in dirs if d.casefold() not in _SKIP_FOLD and not (pathlib.Path(current) / d).is_symlink() and not (hasattr(pathlib.Path(current) / d, "is_junction") and (pathlib.Path(current) / d).is_junction()))
            cp = pathlib.Path(current)
            for name in sorted(files):
                p = cp / name
                try:
                    rel = p.relative_to(self.workspace).as_posix()
                    safe_path(self.workspace, rel)
                    out.append(rel)
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
                txt = ensure_source_text(raw.decode("utf-8"))
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
        self.read_identity.clear()
        result = {}
        limit = int(self.config.get("context_max_bytes_per_file", 20000))
        max_files = int(self.config.get("context_max_files", 40))
        for rel in paths[:max_files]:
            try:
                p = safe_path(self.workspace, rel)
            except ValueError:
                continue
            self.read_identity[rel] = identity(self.workspace, rel)
            if p.is_file():
                raw = p.read_bytes()
                clipped = len(raw) > limit
                use = raw[:limit] if clipped else raw
                ensure_source_text(read_text(p))
                txt = use.decode("utf-8", errors="ignore")
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
        return ensure_source_text(read_text(p)) if p.is_file() else ""

    def validate_edits(self, edits: list[dict], allowed_files: set[str]) -> tuple[dict[str, str | None], list[str]]:
        state: dict[str, str | None] = {}
        issues = []
        for e in edits:
            rel, op = e["path"], e["op"]
            if rel in self.read_identity and identity(self.workspace, rel) != self.read_identity[rel]:
                issues.append(f"{rel}: Datei seit dem Lesen verändert")
                continue
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
                elif rel not in self.fully_read or rel not in self.read_identity:
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
            if new is not None and len(new.encode("utf-8")) > int(self.config.get("max_generated_bytes_per_file", 750000)):
                issues.append(f"{rel}: Generierter Inhalt zu groß")
                continue
            if new is not None:
                ensure_source_text(new)
            old = self.current_text(rel) if safe_path(self.workspace, rel).is_file() else None
            if new != old:
                final[rel] = new
        if not issues and not final:
            issues.append("Keine effektive Änderung")
        return final, issues

    def diff_for_state(self, final):
        chunks = []
        for rel, new in sorted(final.items()):
            old = self.current_text(rel)
            exists = safe_path(self.workspace, rel).exists()
            lines = difflib.unified_diff(old.splitlines(keepends=True),
                    ("" if new is None else new).splitlines(keepends=True),
                    fromfile=f"a/{rel}" if exists else "/dev/null",
                    tofile=f"b/{rel}" if new is not None else "/dev/null")
            for line in lines:
                if line.endswith("\n"):
                    chunks.append(line)
                else:
                    chunks.append(line + "\n\\ No newline at end of file\n")
        return "".join(chunks)

    def analyze(self, task, model):
        if self._busy:
            raise RuntimeError("Eine Analyse läuft bereits")
        self._busy = True
        try:
            return self._analyze(task, model)
        finally:
            self._busy = False

    def _analyze(self, task: str, model: str) -> RunResult:
        if not task.strip() or not model.strip():
            raise ValueError("Aufgabe und installiertes Modell sind erforderlich")
        tag = uuid.uuid4().hex
        ensure_source_text(task)
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
        if not isinstance(plan.get("files"), list) or not isinstance(plan.get("plan"), list):
            raise ValueError("Plan hat kein gültiges Dateiverzeichnis")
        files = [relative(x) for x in plan["files"]]
        if len({x.casefold() for x in files}) != len(files):
            raise ValueError("Mehrdeutige Planpfade")
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
            for edit in candidate:
                if edit["path"] not in self.read_identity:
                    # Unread new files may be created; existing files require task context.
                    if identity(self.workspace, edit["path"]) is not None:
                        raise ValueError("Datei nicht im gelesenen Kontext: " + edit["path"])
                    self.read_identity[edit["path"]] = None
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
                receipt["status"] = "proposed"
                binding = {"workspace": str(self.workspace), "before": dict(self.read_identity),
                           "final_sha256": digest(canonical(final)), "tests_sha256": digest(canonical(self.config.get("tests", []))),
                           "diff_sha256": digest(diff.encode("utf-8"))}
                self.pending[tag] = copy.deepcopy(binding)
                receipt["binding"] = binding
                self.runs.mkdir(parents=True, exist_ok=True)
                atomic_bytes(self.runs / f"{tag}.json", canonical(receipt))
                return RunResult(task=task, plan=plan, diff=diff, final_state=final, edits=candidate, receipt=receipt, binding=binding)
            feedback = "\n".join("- " + str(x) for x in review.get("issues", []))
        receipt["status"] = "rejected"
        atomic_bytes(self.runs / f"{tag}.json", canonical(receipt))
        raise RuntimeError("Kein gültiger geprüfter Vorschlag: " + feedback[:3000])

    def run_tests(self):
        profiles = self.config.get("tests", [])
        if not profiles:
            return {"status": "not_configured", "returncode": None, "output": "", "results": []}
        results = []
        for t in profiles:
            if not isinstance(t, dict) or not isinstance(t.get("argv"), list) or not t["argv"] or not all(isinstance(a, str) and a for a in t["argv"]):
                return {"status": "invalid_configuration", "returncode": 126, "output": "Testprofil benötigt eine nichtleere Argumentliste", "results": results}
            argv = t["argv"]
            if pathlib.Path(argv[0]).suffix.lower() in {".cmd", ".bat"}:
                return {"status": "invalid_configuration", "returncode": 126, "output": "Batch-Dateien sind keine direkten Testprogramme", "results": results}
            name = str(t.get("name") or argv[0])
            self.log("Test: " + name)
            try:
                timeout = int(t.get("timeout_sec", 300))
                if timeout < 1 or timeout > 3600:
                    raise ValueError("Test-Timeout außerhalb 1–3600 Sekunden")
                outcome = run_command(argv, self.workspace, timeout)
                row = {**outcome, "name": name,
                       "output": truncate(redact(outcome['output']), int(self.config.get("test_output_max_chars", 12000)))}
            except subprocess.TimeoutExpired:
                row = {"name": name, "returncode": 124, "output": "Timeout"}
            except (OSError, ValueError) as exc:
                row = {"name": name, "returncode": 126, "output": redact(str(exc))}
            results.append(row)
            if row["returncode"] != 0:
                return {**row, "status": "timed_out" if row["returncode"] == 124 else "failed", "results": results}
        return {"status": "passed", "returncode": 0, "output": "\n".join(r["output"] for r in results), "results": results}

    def claim_proposal(self, result):
        tag = result.receipt.get("tag")
        expected = self.pending.pop(tag, None)
        if expected is None:
            raise ValueError("Vorschlag fehlt, ist verworfen oder bereits verbraucht")
        if expected != result.binding or expected["workspace"] != str(self.workspace):
            raise ValueError("Vorschlag gehört zu einem anderen Workspace")
        if expected["final_sha256"] != digest(canonical(result.final_state)) or expected["diff_sha256"] != digest(result.diff.encode("utf-8")):
            raise ValueError("Vorschlag wurde nach der Prüfung verändert")
        if expected["tests_sha256"] != digest(canonical(self.config.get("tests", []))):
            raise ValueError("Testkonfiguration verändert; neu analysieren")
        for rel, before in expected["before"].items():
            if identity(self.workspace, rel) != before:
                raise ValueError("Datei seit der Vorschau verändert: " + rel)
        return expected

    def apply(self, result):
        expected = self.claim_proposal(result)
        tag = result.receipt["tag"]
        lock = self.workspace / ".codestudio-apply.lock"
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        tx = WorkspaceTransaction(self.workspace, self.backups, tag)
        receipt = copy.deepcopy(result.receipt)
        try:
            os.write(fd, tag.encode("ascii"))
            for rel, new in result.final_state.items():
                if identity(self.workspace, rel) != expected["before"][rel]:
                    raise ValueError("Datei während Anwendung verändert: " + rel)
                if new is None:
                    tx.delete(rel)
                else:
                    tx.write(rel, new)
            receipt["written"] = list(result.final_state)
            test = self.run_tests()
            receipt["test"] = test
            if test["returncode"] not in (None, 0):
                receipt["rollback"] = tx.rollback()
                receipt["status"] = "rolled-back"
            else:
                for rel, new in result.final_state.items():
                    if identity(self.workspace, rel) != (digest(new.encode("utf-8")) if new is not None else None):
                        raise RuntimeError("Datei nach Tests verändert: " + rel)
                receipt["status"] = "applied" if test["returncode"] == 0 else "applied-untested"
            receipt["after"] = {p: identity(self.workspace, p) for p in result.final_state}
            self.runs.mkdir(parents=True, exist_ok=True)
            atomic_bytes(self.runs / f"{tag}.json", canonical(receipt))
            return receipt
        except Exception as exc:
            receipt["error"] = redact(str(exc))
            if isinstance(exc, ProcessTreeUncertain):
                receipt['status'] = 'recovery-required-process-tree'
            else:
                try:
                    receipt["rollback"] = tx.rollback()
                    receipt["status"] = "error-rolled-back"
                except Exception as rollback_error:
                    receipt["status"] = "rollback-conflict"
                    receipt["rollback_error"] = redact(str(rollback_error))
            try:
                atomic_bytes(self.runs / f"{tag}.json", canonical(receipt))
            except OSError:
                pass  # Independent recovery receipt is always attempted below.
            raise
        finally:
            try:
                # Keep a recovery receipt next to the verified preimage even if runs/ failed.
                atomic_bytes(tx.root / 'effect-receipt.json', canonical(receipt))
            finally:
                os.close(fd)
                if receipt.get('status') != 'recovery-required-process-tree':
                    lock.unlink()
