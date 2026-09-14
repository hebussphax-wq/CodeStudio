"""Shared local workspace rules, byte identities and recoverable transactions."""
from __future__ import annotations
import hashlib
import json
import os
import pathlib
import re
import tempfile

BLOCKED = {'.git', '.hg', '.svn', 'node_modules', '__pycache__', '.venv', 'venv',
           'env', 'dist', 'build', 'target', '.next', '.nuxt', '.pytest_cache',
           '.mypy_cache', '.ruff_cache', '.tox', 'coverage', '.idea', '.aistudio',
           '.codestudio', '.codestudio-apply.lock', 'runs', 'backups', '.ssh', '.aws', '.azure'}
SENSITIVE = re.compile(r'(^\.env($|\.)|secret|credential|private[_-]?key|^id_rsa|^id_ed25519|\.pem$|\.pfx$|^settings\.json$)', re.I)

def digest(data):
    return hashlib.sha256(data).hexdigest()

def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')

def relative(rel):
    if not isinstance(rel, str) or not rel or rel != rel.strip():
        raise ValueError('Ungültiger relativer Pfad')
    value = rel.replace('\\', '/')
    parts = value.split('/')
    if any(not p or p in {'.', '..'} or p.endswith((' ', '.')) or ':' in p or
           any(ord(c) < 32 for c in p) or re.match(r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\.|$)', p, re.I)
           for p in parts):
        raise ValueError('Unsicherer Pfad: ' + rel)
    if any(p.casefold() in BLOCKED or SENSITIVE.search(p) for p in parts):
        raise ValueError('Geschützter Pfad: ' + rel)
    return value

def safe_path(workspace, rel, write=False):
    rel = relative(rel)
    root = pathlib.Path(workspace).resolve(strict=True)
    p = root
    for part in rel.split('/'):
        p = p / part
        if p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()):
            raise ValueError('Links sind keine CodeStudio-Ziele: ' + rel)
        if p.exists() and p.is_file() and p.stat().st_nlink > 1:
            raise ValueError('Hardlink ist kein CodeStudio-Ziel: ' + rel)
    p.resolve().relative_to(root)
    if p.exists() and not p.is_file():
        raise ValueError('Kein Datei-Ziel: ' + rel)
    return p

def identity(workspace, rel):
    p = safe_path(workspace, rel)
    return digest(p.read_bytes()) if p.is_file() else None

def read_text(p):
    data = p.read_bytes()
    if b'\0' in data:
        raise ValueError('Binärdatei wird nicht als Quelltext bearbeitet')
    return data.decode('utf-8')

def atomic_bytes(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.codestudio-', dir=p.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def redact(text):
    text = re.sub(r'-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----', '[REDACTED PRIVATE KEY]', str(text))
    text = re.sub(r'(?i)(bearer\s+)[a-z0-9._~+/=-]+', r'\1[REDACTED]', str(text))
    text = re.sub(r'\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{12,}', '[REDACTED]', text)
    return re.sub(r'(?i)((?:api[_-]?key|password|secret|token)[\"\x27]?\s*[=:]\s*[\"\x27]?)[^\s\"\x27,;]+', r'\1[REDACTED]', text)

def ensure_source_text(text):
    if redact(text) != text:
        raise ValueError('Datei enthält mutmaßliche Zugangsdaten; aus dem Coding-Kontext ausgeschlossen')
    return text

class WorkspaceTransaction:
    def __init__(self, workspace, backup_root, tag):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', tag):
            raise ValueError('Ungültige Lauf-ID')
        self.workspace = pathlib.Path(workspace).resolve(strict=True)
        self.root = pathlib.Path(backup_root) / tag
        self.files = {}
        self.created_dirs = []

    def manifest(self):
        atomic_bytes(self.root / 'manifest.json', canonical({'workspace': str(self.workspace), 'files': self.files,
                                                             'created_dirs': self.created_dirs}))

    def touch(self, rel):
        rel = relative(rel)
        if rel in self.files:
            return
        p = safe_path(self.workspace, rel, True)
        before = p.read_bytes() if p.exists() else None
        entry = {'existed_before': before is not None, 'sha256_before': digest(before) if before is not None else None,
                 'sha256_after': digest(before) if before is not None else None}
        if before is not None:
            atomic_bytes(self.root / 'files' / rel, before)
            if digest((self.root / 'files' / rel).read_bytes()) != entry['sha256_before']:
                raise RuntimeError('Sicherung stimmt nicht mit Original überein')
        self.files[rel] = entry
        self.manifest()

    def write(self, rel, content):
        self.touch(rel)
        p = safe_path(self.workspace, rel, True)
        missing = []
        parent = p.parent
        while not parent.exists():
            missing.append(parent.relative_to(self.workspace).as_posix())
            parent = parent.parent
        self.created_dirs.extend(x for x in missing if x not in self.created_dirs)
        atomic_bytes(p, content.encode('utf-8'))
        self.files[rel]['sha256_after'] = digest(content.encode('utf-8'))
        self.manifest()
        if identity(self.workspace, rel) != self.files[rel]['sha256_after']:
            raise RuntimeError('Schreibprüfung fehlgeschlagen: ' + rel)

    def delete(self, rel):
        self.touch(rel)
        safe_path(self.workspace, rel, True).unlink()
        self.files[rel]['sha256_after'] = None
        self.manifest()

    def rollback(self):
        # Check all files first. Never overwrite a concurrent user edit with a backup.
        for rel, entry in self.files.items():
            if identity(self.workspace, rel) not in (entry['sha256_after'], entry['sha256_before']):
                raise RuntimeError('Rollback-Konflikt; Sicherung erhalten: ' + rel)
            if entry['existed_before'] and digest((self.root / 'files' / rel).read_bytes()) != entry['sha256_before']:
                raise RuntimeError('Rollback-Sicherung beschädigt: ' + rel)
        for rel, entry in self.files.items():
            dst = safe_path(self.workspace, rel, True)
            if identity(self.workspace, rel) == entry['sha256_before']:
                continue
            if entry['existed_before']:
                atomic_bytes(dst, (self.root / 'files' / rel).read_bytes())
            elif dst.exists():
                dst.unlink()
            if identity(self.workspace, rel) != entry['sha256_before']:
                raise RuntimeError('Rollback-Rückleseprüfung fehlgeschlagen: ' + rel)
            entry['restored'] = True
            self.manifest()
        for rel in sorted(self.created_dirs, key=lambda s: s.count('/'), reverse=True):
            p = self.workspace / rel
            if p.is_dir() and not any(p.iterdir()):
                p.rmdir()
        return list(self.files)
