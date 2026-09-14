# KI-Codestudio v0.3.1

Lokales Multi-Rollen-Codestudio für Windows + NVIDIA + Ollama.

**Eingabe → Scout → Planer → Coder → Reviewer → Diff → Freigabe → Transaktion → Tests → Reparatur oder Rollback → Receipt**

- nur Python-Standardbibliothek
- keine Cloud
- schreibt nur im Workspace
- zeigt den Diff **vor** dem Schreiben
- jede Aufgabe läuft in einer Transaktion (Snapshot + Rollback)
- Git fasst deine eigenen uncommitteten Dateien nicht an

## Start

Voraussetzungen: Windows 10/11, NVIDIA-Treiber, [Ollama](https://ollama.com), Python 3.11+.

```powershell
git clone https://github.com/hebussphax-wq/CodeStudio.git
cd CodeStudio
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1 -VramGB 16
```

Ollama danach einmal beenden und neu starten (`OLLAMA_MAX_LOADED_MODELS=1`).

```powershell
python -m unittest tests.test_safety -v
python studio.py --doctor
python studio.py "Füge in api.py einen /health-Endpunkt hinzu"
```

Ohne Argument startet `studio.py` eine Eingabeschleife. `start.cmd` macht dasselbe.

| Flag | Wirkung |
|---|---|
| `--dry-run` | Diff erzeugen, nichts schreiben |
| `--yes` | nach Review ohne Rückfrage anwenden |
| `--doctor` | Ollama, GPU, Modelle prüfen |
| `--model NAME` | Modell nur für diesen Lauf |

Workspace: `.\workspace` oder Pfad in `config.json`.

## Modelle

Ein Modell für alle Rollen. Auf 8–16 GB passt nur eines gleichzeitig.

| VRAM | Standard | Kontext |
|---|---|---|
| 8 GB | `qwen2.5-coder:7b` | 8192 |
| 12 GB | `qwen2.5-coder:14b` | 12288 |
| 16 GB | `gpt-oss:20b` | 16384 |
| 24 GB | `qwen3-coder:30b` | 16384 |

`qwen3-coder:30b` (~19 GB) braucht auf 16 GB CPU-Offload. Nicht der Default.

## Sicherheit

- nur relative Pfade, kein `..`, keine Absoluten
- `.git`, `node_modules`, Venv, Build/Cache gesperrt (Windows: auch `.GIT`)
- Whole-File-Write auf **gekürzt gelesene** Dateien wird abgelehnt
- Coder darf nur Plan-Dateien schreiben (`enforce_plan_scope`)
- atomare Writes (`os.replace`)
- bei Testfehler oder Exception nach dem Schreiben: Rollback der ganzen Aufgabe
- Tests nur aus `config.json` (`tests[].argv` oder `test_command`), nie aus Modell-Output
- `git add -A` gibt es nicht. Optionaler Commit nur der geänderten Pfade

Eigene Tests:

```json
"tests": [
  { "name": "unit", "argv": ["python", "-m", "pytest", "-q"], "timeout_sec": 300 }
]
```

## Grenzen (absichtlich)

Keine autonome Shell, kein Internet, kein Tool-Calling, keine GUI, kein semantischer Index.

Grosse Dateien werden für den Prompt gekürzt. Der Coder darf sie dann nicht als Ganzes überschreiben – die Datei teilen oder `context_max_bytes_per_file` erhöhen.

## Dateien

```text
studio.py          Pipeline
config.json        Modelle, Limits, Tests
setup.ps1          VRAM-Profil + ollama pull
start.cmd          interaktive Schleife
tests/             Safety-Tests ohne Ollama
workspace/         dein Projekt
runs/              Receipts
backups/           Transaktions-Snapshots
```
