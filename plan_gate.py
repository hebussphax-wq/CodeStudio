"""SoftKI plan gate: executable checks before coder (script-before-model)."""
from __future__ import annotations

import pathlib
import re
from typing import Any

QUANTIFIER_RE = re.compile(
    r"(?i)\b("
    r"jede|jeder|jedes|alle|allem|allen|mindestens|höchstens|genau|"
    r"erreichbar|verbunden|innerhalb|ausserhalb|nie|niemals|"
    r"every|all|at\s+least|at\s+most|exactly|reachable|connected|within|outside|never"
    r")\b"
)


def normalize_acceptance(raw: Any) -> dict:
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
            checks.append(row)
        elif isinstance(path, str) and path.strip():
            checks.append({"name": name, "path": path.strip().replace("\\", "/")})
        else:
            raise ValueError("Check braucht argv oder path.")
    prose = [str(x).strip() for x in prose_in if str(x).strip()]
    return {"checks": checks, "prose": prose}


def lint_plan_acceptance(plan: dict) -> None:
    """Reject prose with quantifiers when no executable checks exist."""
    acc = normalize_acceptance(plan.get("acceptance"))
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
    p = (workspace / rel).resolve()
    try:
        p.relative_to(workspace.resolve())
    except ValueError as exc:
        raise ValueError("Check-Pfad ausserhalb Workspace: " + rel) from exc
    if not p.is_file():
        raise ValueError("Check-Datei fehlt: " + rel)
    suf = p.suffix.lower()
    if suf == ".py":
        if "tests" in p.parts:
            mod = ".".join(p.with_suffix("").relative_to(workspace).parts)
            return {
                "name": name,
                "argv": ["python", "-m", "unittest", mod, "-v"],
                "timeout_sec": 300,
            }
        return {"name": name, "argv": ["python", str(p)], "timeout_sec": 300}
    if suf in (".cjs", ".mjs", ".js"):
        return {"name": name, "argv": ["node", "--test", str(p)], "timeout_sec": 300}
    raise ValueError("Unsupported check path type: " + rel)


def ensure_checks(
    workspace: pathlib.Path,
    plan: dict,
    existing_tests: list | None = None,
) -> list[dict]:
    """Return runnable test profiles: config tests and/or plan checks."""
    existing = list(existing_tests or [])
    acc = normalize_acceptance(plan.get("acceptance"))
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
