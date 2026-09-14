# KI-Codestudio v0.3 (Ollama, Windows, NVIDIA)

Lokales, kontrolliertes Multi-Rollen-Codestudio:

**Eingabe → Scout → Planer → Coder → Reviewer → Diff → Freigabe → Anwenden → Tests → (Reparatur) → Receipt**

- läuft lokal über Ollama
- Python-Stdlib only
- keine Cloud notwendig
- KI darf nur innerhalb des gewählten Workspace schreiben
- Änderungen werden **vor** dem Schreiben als Unified Diff gezeigt
- vor jedem Anwenden wird ein dateibezogenes Backup erstellt
- in Git-Repositories wird zusätzlich ein Checkpoint-Commit versucht
- Tests laufen nur aus deinem eigenen `test_command` in `config.json`

## 1. Hardware-/Modellprofil (2026)

Ein Modell für alle drei Rollen – auf 8–16 GB passt nur eines gleichzeitig ins VRAM.

| VRAM | Standard (`setup.ps1`) | `num_ctx` | Fallback |
|---|---|---|---|
| 8 GB | `qwen2.5-coder:7b` | 8192 | — |
| 12 GB | `qwen2.5-coder:14b` | 12288 | `qwen2.5-coder:7b` |
| 16 GB | `gpt-oss:20b` | 16384 | `qwen2.5-coder:14b` |
| 24 GB | `qwen3-coder:30b` | 16384 | `gpt-oss:20b` |

`qwen2.5-coder` ist die vorige Generation, auf 8–12 GB aber weiterhin der beste dichte Coder, der wirklich passt.

Nicht als Standard unter 24 GB:

- `qwen3-coder:30b` (~19 GB Gewichte) braucht Kopf für KV-Cache. Auf 16 GB nur mit CPU-Offload.
- `qwen3-coder-next` (~52 GB) erst mit viel System-RAM (praktisch 64 GB+).

## 2. Installation

Voraussetzungen: Windows 10/11, NVIDIA-Treiber, Ollama für Windows, Python 3.11+ im PATH.

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1 -VramGB 16
python studio.py --doctor
python studio.py "Füge in api.py einen /health-Endpunkt hinzu"
```

Ollama danach einmal vollständig beenden und neu starten, damit `OLLAMA_MAX_LOADED_MODELS=1` gilt.

Workspace: `.\workspace` oder Pfad in `config.json`.

## 3. Start

```powershell
python studio.py "Aufgabe"
python studio.py
python studio.py "Aufgabe" --dry-run
python studio.py "Aufgabe" --yes
python studio.py --doctor
python studio.py "Aufgabe" --model qwen2.5-coder:14b
```

## 4. Neu in v0.3

- **Scout:** Stichwortsuche vor dem Planer (kein Embedding).
- **Import-Kontext:** direkte Python-/JS-Importe der gewählten Dateien.
- **Test-Reparatur:** bei rotem `test_command` eine Coder/Reviewer-Runde.
- **Rollback:** bei endgültig roten Tests Restore aus dem ersten Backup.
- **Git-Branch pro Auftrag:** optional `git_branch_per_task`.
