"""SoftKI plan gate: executable checks before coder (script-before-model)."""
from __future__ import annotations

import os
import shlex
import pathlib
import re
import sys
from typing import Any


# Concrete host Path class (captured at import). Unit tests may patch os.name to "nt"
# on Linux; pathlib.Path would then try WindowsPath and fail — use _HOST_PATH instead.
_HOST_PATH = pathlib.WindowsPath if os.name == "nt" else pathlib.PosixPath


def _workspace_rel(workspace: pathlib.Path, path: pathlib.Path) -> pathlib.Path:
    """Relative path under workspace; tolerate Windows 8.3 vs long-path resolve mismatch."""
    ws = _HOST_PATH(os.path.realpath(str(workspace)))
    target = _HOST_PATH(os.path.realpath(str(path)))
    try:
        return target.relative_to(ws)
    except ValueError as exc:
        raise ValueError("Check-Pfad ausserhalb Workspace: " + str(path)) from exc

QUANTIFIER_RE = re.compile(
    r"(?i)\b("
    r"jede|jeder|jedes|alle|allem|allen|mindestens|höchstens|genau|"
    r"erreichbar|verbunden|innerhalb|ausserhalb|nie|niemals|"
    r"every|all|at\s+least|at\s+most|exactly|reachable|connected|within|outside|never"
    r")\b"
)

# Unix-only first tokens that SoftKI planners sometimes emit; rewrite on Windows.
_UNIX_ONLY_CMDS = frozenset({"ls", "cat", "true", "false", "test", "grep", "egrep", "fgrep"})


def _path_exists_argv(workspace: pathlib.Path, rel: str) -> list[str]:
    """Build python argv that exits 0 iff workspace-relative path is a file."""
    rel_n = rel.replace("\\", "/").lstrip("./")
    p = _HOST_PATH(os.path.realpath(str(workspace / rel_n)))
    try:
        _workspace_rel(workspace, p)
    except ValueError as exc:
        raise ValueError("Check-Pfad ausserhalb Workspace: " + rel_n) from exc
    code = (
        "import pathlib,sys;"
        f"sys.exit(0 if pathlib.Path({str(p)!r}).is_file() else 1)"
    )
    return [sys.executable, "-c", code]


def _grep_content_argv(workspace: pathlib.Path, argv: list[str]) -> list[str]:
    """Convert grep-like argv to python -c content check (exit 0/1), no shell."""
    args = list(argv[1:])
    fixed = False
    while args and args[0].startswith("-"):
        opt = args.pop(0)
        if opt in ("-q", "--quiet", "--silent"):
            pass
        elif opt in ("-F", "--fixed-strings"):
            fixed = True
        elif opt in ("-e", "--regexp") and args:
            break
        elif opt == "--":
            break
    if not args:
        raise ValueError("grep-Check braucht Muster und Datei.")
    pattern = args[0]
    if len(args) < 2:
        raise ValueError("grep-Check braucht Dateipfad.")
    rel = args[1].replace("\\", "/").lstrip("./")
    p = _HOST_PATH(os.path.realpath(str(workspace / rel)))
    try:
        _workspace_rel(workspace, p)
    except ValueError as exc:
        raise ValueError("Check-Pfad ausserhalb Workspace: " + rel) from exc
    code = (
        "import pathlib,sys,re;"
        f"p=pathlib.Path({str(p)!r});"
        "t=p.read_text(encoding='utf-8',errors='replace') if p.is_file() else '';"
        f"pat={pattern!r}; fixed={fixed!r};"
        "ok=(pat in t) if fixed else bool(re.search(pat,t));"
        "sys.exit(0 if ok else 1)"
    )
    return [sys.executable, "-c", code]


def rewrite_check_for_windows(check: dict, workspace: pathlib.Path | None = None) -> dict:
    """On Windows, rewrite Unix argv checks to path or python argv forms."""
    if os.name != "nt":
        return check
    if "argv" not in check:
        return check
    argv = list(check["argv"])
    if not argv:
        return check
    head = pathlib.Path(argv[0]).name.lower()
    if head.endswith(".exe"):
        head = head[:-4]
    name = check.get("name") or "check"
    timeout = check.get("timeout_sec", 300)

    # test -f / test -e PATH -> path-check form (consumed by _bind_path_check)
    if head == "test" and len(argv) >= 3 and argv[1] in ("-f", "-e"):
        rel = argv[2].replace("\\", "/").lstrip("./")
        return {"name": name, "path": rel}

    # grep -> python content check (needs workspace; defer if unknown)
    if head in ("grep", "egrep", "fgrep"):
        if workspace is None:
            return check
        return {
            "name": name,
            "argv": _grep_content_argv(workspace, argv),
            "timeout_sec": timeout if isinstance(timeout, int) else 300,
        }

    if head == "true" and len(argv) == 1:
        return {
            "name": name,
            "argv": [sys.executable, "-c", "raise SystemExit(0)"],
            "timeout_sec": 60,
        }

    if head == "false" and len(argv) == 1:
        return {
            "name": name,
            "argv": [sys.executable, "-c", "raise SystemExit(1)"],
            "timeout_sec": 60,
        }

    if head in ("ls", "cat") and len(argv) >= 2:
        rel = None
        for a in reversed(argv[1:]):
            if not a.startswith("-"):
                rel = a.replace("\\", "/").lstrip("./")
                break
        if rel:
            return {"name": name, "path": rel}

    if head in _UNIX_ONLY_CMDS:
        raise ValueError(
            "SoftKI-Check auf Windows: Unix-Befehl nicht nutzbar ("
            + argv[0]
            + "). path oder python argv verwenden."
        )

    return check


def normalize_acceptance(raw: Any, workspace: pathlib.Path | None = None) -> dict:
    """Accept legacy list[str] or SoftKI {checks, prose}."""
    if raw is None:
        return {"checks": [], "prose": []}
    if isinstance(raw, list):
        prose = [str(x).strip() for x in raw if str(x).strip()]
        return {"checks": [], "prose": prose}
    if not isinstance(raw, dict):
        raise ValueError("Plan-Acceptance muss Liste oder Objekt mit checks/prose sein.")
    checks_in = raw.get("checks", [])
    prose_in = raw.get("prose", raw.get("acceptance", []))
    if not isinstance(checks_in, list):
        raise ValueError("acceptance.checks muss eine Liste sein.")
    if not isinstance(prose_in, list):
        raise ValueError("acceptance.prose muss eine Liste sein.")
    checks = []
    for item in checks_in:
        if isinstance(item, str) and item.strip():
            checks.append({"name": item.strip(), "path": item.strip()})
            continue
        if not isinstance(item, dict):
            raise ValueError("Jeder Check braucht name und argv oder path.")
        name = str(item.get("name") or "").strip() or "check"
        argv = item.get("argv")
        path = item.get("path")
        if argv is not None:
            if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
                raise ValueError("Check.argv muss nichtleere String-Liste sein.")
            if pathlib.Path(argv[0]).suffix.lower() in (".bat", ".cmd"):
                raise ValueError("Direktes Testprogramm statt Batch-Datei.")
            row = {"name": name, "argv": list(argv)}
            to = item.get("timeout_sec", 300)
            if isinstance(to, int) and 1 <= to <= 3600:
                row["timeout_sec"] = to
            else:
                row["timeout_sec"] = 300
            row = rewrite_check_for_windows(row, workspace)
            checks.append(row)
        elif isinstance(path, str) and path.strip():
            checks.append({"name": name, "path": path.strip().replace("\\", "/")})
        else:
            raise ValueError("Check braucht argv oder path.")
    prose = [str(x).strip() for x in prose_in if str(x).strip()]
    return {"checks": checks, "prose": prose}


# Prose → check promotion (script-before-model): extract runnable cmds / path exists.
_CMD_STOP_RE = re.compile(
    r"(?i)\s+(?:passes?|passing|returns?|succeeds?|fails?|failed|ok|okay|"
    r"successfully|with\s+exit|and\s+then|muss|must|should|soll)\b.*$"
)
_OUTCOME_ONLY_RE = re.compile(
    r"(?i)^(?:passes?|passing|returns?|succeeds?|fails?|failed|ok|okay|"
    r"successfully|with\s+exit|and\s+then|muss|must|should|soll)\b.*$"
)
_CMD_HEADS = frozenset({"python", "python3", "py", "node", "pytest"})
_PATH_EXISTS_RE = re.compile(
    r"(?i)(?:\bfile\s+([A-Za-z0-9_./\\-]+\.[A-Za-z0-9]+)\b)"
    r"|(?:\b([A-Za-z0-9_./\\-]+\.[A-Za-z0-9]+)\s+exists\b)"
)
_CMD_FIND_RE = re.compile(
    r"(?i)(?:^|[\s`\"'])((?:python3?|py|node|pytest)\b[^\n]*)"
)


def _extract_runnable_argv(line: str) -> list[str] | None:
    """Pull python/node/pytest argv from a prose line; strip trailing outcome prose."""
    stripped = line.strip()
    m = _CMD_FIND_RE.search(stripped)
    if not m and not re.match(r"(?i)^(?:python3?|py|node|pytest)\b", stripped):
        return None
    raw = m.group(1) if m else stripped
    raw = raw.strip().strip("`\"'")
    raw = _CMD_STOP_RE.sub("", raw).strip().rstrip("`\"',.;:")
    if not raw:
        return None
    try:
        argv = shlex.split(raw, posix=True)
    except ValueError:
        argv = raw.split()
    if not argv:
        return None
    head = pathlib.Path(argv[0]).name.lower()
    if head.endswith(".exe"):
        head = head[:-4]
    if head not in _CMD_HEADS:
        return None
    if head in ("python", "python3", "py") and len(argv) < 2:
        return None
    if head == "node" and len(argv) < 2:
        return None
    return argv


def _extract_path_from_prose(line: str) -> str | None:
    """Detect 'file X' / 'X exists' path mentions in prose."""
    m = _PATH_EXISTS_RE.search(line)
    if not m:
        return None
    rel = (m.group(1) or m.group(2) or "").strip().replace("\\", "/")
    if not rel or ".." in pathlib.PurePosixPath(rel).parts:
        return None
    return rel.lstrip("./")


def promote_executable_prose(acc: dict) -> dict:
    """Promote runnable command / path-exists prose into acceptance.checks.

    Mutates and returns acc. Leaves non-command prose; drops command-only lines
    once turned into checks. Idempotent if checks already cover the same argv/path.
    """
    if not isinstance(acc, dict):
        return acc
    checks = list(acc.get("checks") or [])
    prose_in = list(acc.get("prose") or [])
    seen_argv: set[tuple[str, ...]] = set()
    seen_path: set[str] = set()
    for c in checks:
        if isinstance(c, dict) and "argv" in c and isinstance(c["argv"], list):
            seen_argv.add(tuple(c["argv"]))
        if isinstance(c, dict) and isinstance(c.get("path"), str) and c["path"].strip():
            seen_path.add(c["path"].strip().replace("\\", "/").lstrip("./"))

    prose_out: list[str] = []
    for line in prose_in:
        text = str(line).strip()
        if not text:
            continue
        argv = _extract_runnable_argv(text)
        if argv:
            key = tuple(argv)
            if key not in seen_argv:
                name = "promoted-" + pathlib.Path(argv[0]).name.lower()
                if len(argv) >= 3 and argv[1] == "-m":
                    name = "promoted-" + argv[2]
                elif len(argv) >= 2:
                    name = "promoted-" + pathlib.Path(argv[-1]).name
                checks.append({"name": name, "argv": list(argv), "timeout_sec": 300})
                seen_argv.add(key)
            # Keep residual prose after stripping the command, else drop command-only.
            residual = text
            joined = " ".join(argv)
            idx = residual.lower().find(joined.lower())
            if idx >= 0:
                residual = (residual[:idx] + residual[idx + len(joined) :]).strip(" -–—,:;")
                residual = _CMD_STOP_RE.sub("", residual).strip(" -–—,:;")
            else:
                residual = _CMD_STOP_RE.sub("", residual)
                residual = re.sub(
                    r"(?i)^(?:[`\"']?)(?:python3?|py|node|pytest)\b[^\n]*",
                    "",
                    residual,
                ).strip(" -–—,:;")
            residual = _OUTCOME_ONLY_RE.sub("", residual).strip(" -–—,:;")
            if residual and not _extract_runnable_argv(residual):
                prose_out.append(residual)
            continue

        rel = _extract_path_from_prose(text)
        if rel:
            if rel not in seen_path:
                checks.append({"name": "promoted-" + pathlib.Path(rel).name, "path": rel})
                seen_path.add(rel)
            # Path-only existence lines drop; richer prose stays.
            path_only = re.fullmatch(
                r"(?i)(?:file\s+)?"
                + re.escape(rel)
                + r"(?:\s+exists)?[.!]?",
                text.replace("\\", "/"),
            )
            if path_only:
                continue
            prose_out.append(text)
            continue

        prose_out.append(text)

    acc["checks"] = checks
    acc["prose"] = prose_out
    return acc


def lint_plan_acceptance(plan: dict) -> None:
    """Reject prose with quantifiers when no executable checks exist.

    Promotes runnable command / path-exists prose into checks first so planner
    lines like 'python -m unittest … passes all tests' do not stall the gate.
    """
    acc = normalize_acceptance(plan.get("acceptance"))
    promote_executable_prose(acc)
    plan["acceptance"] = acc
    if acc["checks"]:
        return
    for line in acc["prose"]:
        if QUANTIFIER_RE.search(line):
            raise ValueError(
                "SoftKI-Plan-Gate: Prosa mit Quantor ohne ausführbaren Check: " + line[:200]
            )


def _bind_path_check(workspace: pathlib.Path, name: str, rel: str) -> dict:
    rel = rel.replace("\\", "/").lstrip("./")
    p = _HOST_PATH(os.path.realpath(str(workspace / rel)))
    try:
        _workspace_rel(workspace, p)
    except ValueError as exc:
        raise ValueError("Check-Pfad ausserhalb Workspace: " + rel) from exc
    suf = p.suffix.lower()
    # Existing runnable scripts/tests: bind as executable now.
    # Missing path (incl. .py/.js run outputs): deferred existence so coder can create first.
    if suf in (".py", ".cjs", ".mjs", ".js") and p.is_file():
        if suf == ".py":
            if "tests" in p.parts:
                mod = ".".join(_workspace_rel(workspace, p.with_suffix("")).parts)
                return {
                    "name": name,
                    "argv": ["python", "-m", "unittest", mod, "-v"],
                    "timeout_sec": 300,
                }
            return {"name": name, "argv": ["python", str(p)], "timeout_sec": 300}
        return {"name": name, "argv": ["node", "--test", str(p)], "timeout_sec": 300}
    return {
        "name": name,
        "argv": _path_exists_argv(workspace, rel),
        "timeout_sec": 60,
    }


def ensure_checks(
    workspace: pathlib.Path,
    plan: dict,
    existing_tests: list | None = None,
) -> list[dict]:
    """Return runnable test profiles: config tests and/or plan checks."""
    existing = list(existing_tests or [])
    acc = normalize_acceptance(plan.get("acceptance"), workspace=workspace)
    plan["acceptance"] = acc
    out: list[dict] = []
    seen: set[str] = set()

    def add(profile: dict) -> None:
        key = str(profile.get("argv"))
        if key in seen:
            return
        seen.add(key)
        out.append(profile)

    for t in existing:
        if isinstance(t, dict) and isinstance(t.get("argv"), list) and t["argv"]:
            add({
                "name": t.get("name") or t["argv"][0],
                "argv": list(t["argv"]),
                "timeout_sec": t.get("timeout_sec", 300),
            })

    for c in acc["checks"]:
        # Re-apply Windows rewrite with real workspace (grep needs it)
        c = rewrite_check_for_windows(dict(c), workspace)
        if "argv" in c:
            add({
                "name": c["name"],
                "argv": list(c["argv"]),
                "timeout_sec": c.get("timeout_sec", 300),
            })
        elif "path" in c:
            add(_bind_path_check(workspace, c["name"], c["path"]))

    return out


def acceptance_for_prompt(acc: Any) -> str:
    """Flatten acceptance for LLM prompts."""
    n = normalize_acceptance(acc)
    lines = list(n["prose"])
    for c in n["checks"]:
        if "argv" in c:
            lines.append("CHECK " + c["name"] + ": " + " ".join(c["argv"]))
        else:
            lines.append("CHECK " + c["name"] + ": " + str(c.get("path")))
    return "\n".join(lines)

