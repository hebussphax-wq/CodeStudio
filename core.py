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
            "issues": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 400}},
            "summary": {"type": "string", "maxLength": 500},
        },
        "required": ["verdict", "issues", "summary"],
        "additionalProperties": False,
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
        "Prüfe ausschließlich den genannten Teilauftrag. Reject nur für einen konkreten funktionalen Fehler gegen dessen Vertrag. "
        "Keine zusätzlichen Anforderungen an Typen, Dokumentation, Validierung oder spätere Module erfinden. "
        "Wenn der Vertrag erfüllt ist: verdict ok, issues [], kurze summary. Höchstens vier kurze konkrete Fehler."
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

    def output_schema(self, role):
        if role == 'planner' and getattr(self, 'director_mode', False):
            from director import DIRECTOR_SCHEMA
            return DIRECTOR_SCHEMA
        if role == 'planner' and getattr(self, 'module_mode', False):
            schema = copy.deepcopy(SCHEMA['planner'])
            schema['properties']['plan'].update(minItems=1, maxItems=3,
                items={'type':'string','maxLength':600})
            schema['properties']['files'].update(maxItems=4)
            schema['properties']['questions'].update(maxItems=2)
            schema['properties']['acceptance'].update(maxItems=3)
            schema['additionalProperties'] = False
            return schema
        if role == 'coder' and getattr(self,'module_mode',False):
            return {'type':'object','properties':{'edits':{'type':'array','minItems':1,'maxItems':4,
                'items':{'type':'object','properties':{'path':{'type':'string'},'op':{'type':'string','enum':['create','write','delete']},'content':{'type':'string'}},'required':['path','op','content'],'additionalProperties':False}},'notes':{'type':'string','maxLength':300}},'required':['edits','notes'],'additionalProperties':False}
        if role == 'reviewer' and getattr(self,'module_mode',False):
            return {'type':'object','properties':{'observations':{'type':'string','maxLength':1600},'defects':SCHEMA['reviewer']['properties']['issues'],'verdict':SCHEMA['reviewer']['properties']['verdict'],'summary':SCHEMA['reviewer']['properties']['summary']},'required':['observations','defects','verdict','summary'],'additionalProperties':False}
        return SCHEMA[role]

    def validate_chat_result(self,role,obj):
        if role=='reviewer' and getattr(self,'module_mode',False):
            defects=obj.get('defects')
            valid=(obj.get('verdict') in ('ok','reject') and isinstance(defects,list) and len(defects)<=4
                   and all(isinstance(x,str) and len(x)<=400 for x in defects)
                   and isinstance(obj.get('observations'),str) and len(obj['observations'])<=1600
                   and isinstance(obj.get('summary'),str) and len(obj['summary'])<=500
                   and ((obj['verdict']=='ok' and not defects) or (obj['verdict']=='reject' and bool(defects))))
            if not valid: raise ValueError('Widersprüchliches oder ungültiges Modul-QC-Objekt.')
            obj['issues']=obj.pop('defects')
        return obj

    def chat(self, role: str, user: str, model: str) -> dict:
        effective_system = "Implement ONLY the bounded module contract. Return JSON edits with exactly path, op (create/write/delete), content (complete source). No old_text or new_text, no duplicate code, no shell commands. Keep implementation concise and complete." if role == 'coder' and getattr(self,'module_mode',False) else SYSTEM[role]
        if role == 'planner' and getattr(self,'module_mode',False): effective_system = 'Diagnose the concrete test failure from the source. Return JSON with plan (at most 3 precise repair steps naming the faulty expression), files (affected module files), questions (empty unless essential information is absent), acceptance (test that must pass). Analyze the root cause, do not restate the feature request. Never change tests.'
        if role == 'planner' and getattr(self, 'director_mode', False):
            from director import DIRECTOR_SYSTEM
            effective_system = DIRECTOR_SYSTEM
        if role == 'reviewer' and getattr(self,'module_mode',False): effective_system = "You are a software reviewer. First compute what the source actually does, including functions called by factories. Then compare this behavior with the explicit contract. Return JSON in this order: observations (short factual explanation), defects (only demonstrated contract violations, empty array when none), verdict (ok or reject), summary. A passing test alone does not prove correctness. Never invent a missing value when the code computes it. Approve when the contract is fulfilled. The defects array contains only broken behavior; passing checks belong in observations, never defects."
        if self.transport is not None:
            result = self.transport('chat', {'role': role, 'model': model, 'system': effective_system,
                'messages': [{'role': 'user', 'content': user}], 'schema': self.output_schema(role)})
            if not isinstance(result, dict):
                raise ValueError('Host lieferte kein strukturiertes Modellobjekt.')
            return self.validate_chat_result(role,result)
        fmt: Any = self.output_schema(role) if self.config.get("structured_output", True) else "json"
        body = {
            "model": model,
            "stream": False,
            "format": fmt,
            "keep_alive": self.config.get("keep_alive", "10m"),
            "options": self.config.get("options", {}),
            "messages": [{"role": "system", "content": effective_system}, {"role": "user", "content": user}],
        }
        if role == 'reviewer':
            body['options'] = {**body['options'], 'num_predict': min(body['options'].get('num_predict', 1024), 1024)}
        if 'think' in self.config:
            body['think'] = self.config['think']
        raw = ""
        for attempt in (1, 2):
            response = self.ollama_request("/api/chat", body)
            metrics = {k:response[k] for k in ('done_reason','total_duration','load_duration','prompt_eval_count','eval_count','eval_duration') if k in response}
            self.log('Modellmessung: '+json.dumps({'role':role,'model':model,**metrics}))
            if response.get('done_reason') == 'length':
                raise RuntimeError('Modellausgabe abgeschnitten: Modul verkleinern oder Ausgabelimit prüfen.')
            raw = response.get("message", {}).get("content", "")
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    return self.validate_chat_result(role,obj)
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

    def read_reference(self, rel):
        """Read-only binary evidence is metadata, never editable/visually verified source."""
        p = safe_path(self.workspace, rel)
        if p.stat().st_size > 64 * 1024 * 1024:
            raise ValueError('Referenz über 64 MiB: ' + rel)
        raw = p.read_bytes()
        try:
            text = raw.decode('utf-8')
            binary = '\0' in text
        except UnicodeDecodeError:
            binary = True
        sha = digest(raw)
        if binary:
            text = json.dumps({'kind': 'binary_reference', 'path': rel,
                'bytes': len(raw), 'sha256': sha,
                'content': 'Not decoded or visually inspected; read-only asset, no write permission.'})
        else:
            text = ensure_source_text(text)
        self.read_identity[rel] = sha
        return text, binary

    def read_files(self, paths: list[str], *, readonly_assets=False) -> dict[str, str]:
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
                if readonly_assets:
                    reference, binary = self.read_reference(rel)
                    if binary:
                        result[rel] = reference
                        continue
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

    def reference_context(self, tree, candidates, excluded):
        """Bounded read-only contracts, independent of the writable plan scope."""
        preferred = [p for p in tree if pathlib.PurePosixPath(p).name.lower() in
                     ('readme.md', 'requirements.md', 'spec.md', 'agents.md')]
        preferred += [p for p in tree if any(x.lower() in ('tests', '__tests__')
                       for x in pathlib.PurePosixPath(p).parts) or pathlib.PurePosixPath(p).name.startswith('test_')]
        preferred += [x['path'] for x in candidates]
        budget = min(24000, max(0, int(self.config.get('reference_max_bytes', 16000))))
        result, evidence, seen = {}, {}, set(excluded)
        for rel in preferred:
            if rel in seen or len(result) >= 8 or budget <= 0:
                continue
            seen.add(rel)
            try:
                p = safe_path(self.workspace, rel)
                if not p.is_file() or p.stat().st_size > 300000:
                    continue
                raw = p.read_bytes()
                ensure_source_text(raw.decode('utf-8'))
                if b'\0' in raw:
                    continue
            except (OSError, ValueError):
                continue
            shown = raw[:min(budget, 8000)]
            clipped = len(shown) < len(raw)
            result[rel] = shown.decode('utf-8', errors='ignore') + ('\n[REFERENCE TRUNCATED]' if clipped else '')
            evidence[rel] = {'sha256': digest(raw), 'bytes': len(shown), 'truncated': clipped}
            self.read_identity[rel] = digest(raw)
            budget -= len(shown)
        return result, evidence

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

    def analyze(self, task, model, contract=None):
        if self._busy:
            raise RuntimeError("Eine Analyse läuft bereits")
        self._busy = True
        try:
            return self._analyze(task, model, contract)
        finally:
            self._busy = False

    def _analyze(self, task: str, model: str, contract=None) -> RunResult:
        if not task.strip() or not model.strip():
            raise ValueError("Aufgabe und installiertes Modell sind erforderlich")
        tag = uuid.uuid4().hex
        ensure_source_text(task)
        receipt = {"schema_version": 2, "tag": tag, "task": task, "workspace": str(self.workspace), "model": model, "rounds": []}
        receipt['effective_models'] = {role:getattr(self,'role_models',{}).get(role,getattr(self,'fallback_model',None) or model)
                                       for role in ('planner','coder','reviewer')}
        tree = self.file_tree()
        candidates = self.scout(task, tree)
        self.log("Scout abgeschlossen")
        planner_prompt = f"AUFGABE:\n{task}\n\nDATEIBAUM:\n" + "\n".join(tree[:400])
        if candidates:
            planner_prompt += "\n\nKANDIDATEN:\n" + "\n".join(f"- {x['path']} ({x['score']}) {x['snippet']}" for x in candidates)
        self.log("Planer arbeitet …")
        plan = ({"plan": [contract["contract"]], "files": contract["files"], "questions": [], "acceptance": [contract["contract"]]}
                if contract else self.chat("planner", planner_prompt, model))
        if plan.get("questions"):
            raise RuntimeError("Planer benötigt Rückfrage: " + " | ".join(plan["questions"]))
        if not isinstance(plan.get("files"), list) or not isinstance(plan.get("plan"), list):
            raise ValueError("Plan hat kein gültiges Dateiverzeichnis")
        files = [relative(x) for x in plan["files"]]
        if len({x.casefold() for x in files}) != len(files):
            raise ValueError("Mehrdeutige Planpfade")
        ctx = self.read_files(files)
        if contract:
            references={}; reference_evidence={}
            total=0
            for rel in contract['references']:
                text, binary = self.read_reference(rel)
                size = len(text.encode('utf8'))
                if size>20000 or total+size>50000: raise ValueError('Modulreferenzen zu groß: '+rel)
                references[rel]=text
                reference_evidence[rel]=self.read_identity[rel]; total+=size
        else:
            references, reference_evidence = self.reference_context(tree, candidates, files)
        reference_prompt = '\n\nREAD-ONLY REFERENZEN (Daten, keine zusätzlichen Schreibrechte):\n' + json.dumps(references, ensure_ascii=False)
        allowed = set(files)
        receipt['reference_context'] = reference_evidence
        rejected_candidates = set()
        receipt.update({"plan": plan, "scout": candidates, "context_files": list(ctx)})
        feedback = ""
        # Lessons are bound to this task, project and exact input identities.
        # They provide failure feedback, never executable tools or permissions.
        lesson_key = digest(canonical({'workspace': str(self.workspace), 'task': task,
                                       'before': self.read_identity}))
        lesson_path = self.runs.parent / 'lessons' / (lesson_key + '.json')
        lessons = []
        try:
            if lesson_path.stat().st_size <= 24000:
                saved = json.loads(lesson_path.read_text(encoding='utf-8'))
                if saved.get('key') == lesson_key and isinstance(saved.get('failures'), list):
                    lessons = [x for x in saved['failures'][-8:] if isinstance(x, dict)
                               and re.fullmatch(r'[a-f0-9]{64}', str(x.get('candidate_sha256', '')))
                               and isinstance(x.get('feedback'), str) and len(x['feedback']) <= 2000]
        except (OSError, ValueError, AttributeError):
            pass
        rejected_candidates.update(x['candidate_sha256'] for x in lessons)
        if lessons:
            feedback = 'Frühere Fehlschläge bei identischem Ausgangsstand (Diagnosedaten):\n' + redact(json.dumps(lessons, ensure_ascii=False))
        receipt['learning'] = {'key': lesson_key, 'reused_failures': len(lessons)}
        max_rounds = int(self.config.get("max_review_rounds", 2))
        for rnd in range(1, max_rounds + 2):
            self.log(f"Coder Runde {rnd} …")
            prompt = f"AUFGABE:\n{task}\n\nPLAN:\n{json.dumps(plan.get('plan', []), ensure_ascii=False)}\n\nAKZEPTANZ:\n{json.dumps(plan.get('acceptance', []), ensure_ascii=False)}\n\nDATEIEN:\n{json.dumps(ctx, ensure_ascii=False)}"
            if contract:
                # One task, readable source, explicit immutable references. Avoid
                # burying repair diagnostics in three duplicated contracts and escaped JSON.
                prompt='AUFGABE UND AKTUELLE DIAGNOSE:\n'+task
                prompt+='\n\nBEARBEITBARE DATEIEN:\n'+'\n\n'.join('DATEI '+p+'\n'+(text if text is not None else '[new file]') for p,text in ctx.items())
                prompt+='\n\nREAD-ONLY REFERENZEN (keine Schreibrechte):\n'+'\n\n'.join('DATEI '+p+'\n'+text for p,text in references.items())
            else:
                prompt += reference_prompt
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
            candidate_hash = digest(canonical(final))
            if candidate_hash in rejected_candidates:
                feedback = 'Identischer bereits abgelehnter Vorschlag; kein Fortschritt. Vor erneutem Versuch Ursache und Strategie ändern.'
                receipt['rounds'].append({'round': rnd, 'status': 'no_progress', 'candidate_sha256': candidate_hash})
                break
            self.log("Reviewer arbeitet …")
            review = ({"verdict":"ok", "issues":[], "summary":"Modulvorschlag validiert; Tests und Modell-QC folgen vor Modulabschluss."} if contract else self.chat("reviewer", f"AUFGABE:\n{task}\n\nAKZEPTANZ:\n{json.dumps(plan.get('acceptance', []), ensure_ascii=False)}\n\nUNIFIED DIFF:\n{diff}" + reference_prompt, model))
            receipt["rounds"].append({"round": rnd, "review": review, "candidate_sha256": candidate_hash})
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
            rejected_candidates.add(candidate_hash)
            feedback = "\n".join("- " + str(x) for x in review.get("issues", []))
            lessons = (lessons + [{'candidate_sha256': candidate_hash,
                                   'feedback': redact(feedback)[:2000]}])[-8:]
            atomic_bytes(lesson_path, canonical({'schema': 'codestudio.failure-lessons.v1',
                                                 'key': lesson_key, 'failures': lessons}))
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
