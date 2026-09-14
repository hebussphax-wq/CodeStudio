# CodeStudio – lokales KI-Coding-Studio für Windows + Ollama

Fertige lokale Windows-App mit **Workspace-Auswahl, Ollama-Modellwahl, Planer, Coder, Reviewer, Diff-Vorschau, Freigabe, Tests und Rollback**.

## Start

1. Ollama und Python 3.11+ installieren.
2. PowerShell im CodeStudio-Ordner:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1
```

3. Ollama einmal komplett neu starten.
4. `start.cmd` doppelklicken.

Alternativ:

```powershell
python codestudio.py
```

## Bedienung

1. Workspace wählen.
2. Ollama-Modell auswählen.
3. Aufgabe eingeben.
4. **Analysieren** drücken.
5. Plan + Diff prüfen.
6. **Änderungen anwenden** drücken.
7. Konfigurierte Tests laufen automatisch. Bei Fehlschlag wird zurückgerollt.

## Diagnose

```powershell
python codestudio.py --doctor
python -m unittest tests.test_core -v
```

## Modellprofile im Setup

- 8 GB VRAM → `qwen2.5-coder:7b`
- 12 GB VRAM → `qwen2.5-coder:14b`
- 16 GB VRAM → `gpt-oss:20b`

Das Modell kann in der GUI jederzeit auf ein anderes installiertes Ollama-Modell umgestellt werden.

## Sicherheitsprinzipien

- nur relative Pfade im Workspace
- `../`, `.git`, `node_modules`, Venv-/Build-/Cache-Verzeichnisse gesperrt
- bestehende Dateien bevorzugt per exaktem `replace`
- Whole-file-write nur nach vollständigem Lesen im selben Task
- atomare Writes
- Task-Snapshot + Rollback
- Tests nur aus `config.json`, immer `shell=False`
- Änderungen werden vor dem Schreiben als Unified Diff gezeigt
- Receipt pro Lauf unter `runs/`

## Tests konfigurieren

```json
"tests": [
  {
    "name": "unit",
    "argv": ["python", "-m", "pytest", "-q"],
    "timeout_sec": 300
  }
]
```
