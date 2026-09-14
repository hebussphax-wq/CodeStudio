# KI-Codestudio v0.3.1 (Ollama, Windows, NVIDIA)

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

Temporär anderes Modell:

```powershell
python studio.py "Aufgabe" --model qwen2.5-coder:14b
```

## 2. Installation

Voraussetzungen:

1. Windows 10/11
2. NVIDIA-Treiber
3. Ollama für Windows
4. Python 3.11+ im PATH

PowerShell im Projektordner:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1
```

Oder direkt mit Profil:

```powershell
.\setup.ps1 -VramGB 8
.\setup.ps1 -VramGB 12
.\setup.ps1 -VramGB 16
.\setup.ps1 -VramGB 24
```

`setup.ps1`:

- prüft Ollama
- setzt `OLLAMA_MAX_LOADED_MODELS=1` auf Benutzerebene
- schreibt Modell und `num_ctx` nach `config.json`
- zieht das Modell

**Wichtig:** Ollama danach einmal vollständig beenden und neu starten, damit `OLLAMA_MAX_LOADED_MODELS=1` gilt.

Dann:

```powershell
python studio.py --doctor
```

## 3. Workspace

Standard: `.\workspace`

Lege dein Projekt dort hinein – oder setze in `config.json`:

```json
"workspace": "D:/Projekte/MeinProjekt"
```

Git-Repo empfohlen, aber nicht zwingend.

## 4. Start

```powershell
python studio.py "Füge in api.py einen /health-Endpunkt hinzu und ergänze Tests."
python studio.py
python studio.py "Refaktoriere die Authentifizierung" --dry-run
python studio.py "Korrigiere den Parser" --yes
python studio.py --doctor
```

`start.cmd` startet die interaktive Schleife.

## 5. Sicherheitsmodell

Das Studio:

- akzeptiert nur relative Pfade im Workspace
- blockiert Traversal und geschützte Verzeichnisse (`.git`, `node_modules`, Venv, Build/Cache)
- begrenzt Anzahl und Grösse KI-generierter Änderungen
- zeigt vor dem Anwenden ein Unified Diff
- sichert nur die betroffenen Dateien nach `backups/`
- führt keine von der KI erfundenen Shell-Befehle aus
- führt nur deinen eigenen `test_command` aus
- schreibt jedes Mal ein Receipt nach `runs/`

## 6. Review-Logik

Bei `reject` bekommt der Coder Aufgabe, Originaldateien, letzten vollständigen Patch und die Beanstandungen. Er muss erneut den **kompletten Ersatz-Patch** liefern.

## 7. Grenzen

Absichtlich kontrolliertes MVP:

- keine autonome Shell
- kein autonomes Internet
- kein Tool-Calling
- kein unendlicher Agentenloop
- kein Repo-Index
- kein GUI

Bekannt:

- Reviewer = dasselbe Modell wie Coder. `test_command` ist die eigentliche Prüfung.
- Grosse Einzeldateien werden auf `context_max_bytes_per_file` gekürzt; das Kontextbudget hält den Prompt unter ~50 % von `num_ctx`.

## 8. Neu in v0.3 (gegen Mock-Ollama in drei Szenarien geprüft)

**Scout** – vor dem Planer: Stichwörter aus der Aufgabe werden per Textsuche gegen Pfade und Inhalte aller Workspace-Dateien gerankt (Top 15 mit Snippet). Der Planer sieht diese Kandidaten zusätzlich zum Dateibaum. Rein deterministisch, kein Embedding-Modell, keine zweite GPU-Last. Zusätzlich werden direkte Importe der gewählten Dateien (Python-Module, relative JS/TS-Importe) automatisch in den Kontext geholt – im Test: Planer wählte `hello.py`, `util.py` kam über `import util` dazu.

**Test-Reparaturschleife** – schlägt `test_command` nach dem Anwenden fehl, bekommt der Coder den aktuellen Dateistand plus Testausgabe und liefert einen Ersatz-Patch, der wieder durch den Reviewer geht. `max_repair_rounds` (Standard 1). Der Tester ist kein LLM, sondern dein Befehl.

**Rollback** – bleiben die Tests nach allen Runden rot, werden die Originaldateien aus dem ersten Backup wiederhergestellt (`rollback_on_test_failure`, Standard true). Der verworfene Patch bleibt im Receipt.

**Git-Branch pro Auftrag** – `git_branch_per_task: true` legt `studio/<tag>` an, macht dort den Checkpoint und committet die KI-Änderung nach grünem Test. Standard false, weil es deinen aktuellen Branch wechselt.

Ohne Testprofil verhält sich das Studio wie v0.2.2 plus Scout.

Nicht enthalten (bewusst): semantischer Index, LLM-Tester, autonome Shell, GUI.

## 9. v0.3.1 – Datenintegrität (Abschlussversion)

Dies ist die Abschlussversion des Studios. Kein weiterer Umbau geplant; offene Punkte sind bewusst nicht gebaut (Abschnitt 10).

**Neues Edit-Format.** Der Coder liefert keine ganzen Dateien mehr, sondern Edits:
- `replace` – `old_text` muss wörtlich und genau einmal in der Datei vorkommen, wird durch `new_text` ersetzt (mehrere pro Datei erlaubt)
- `create` – neue Datei
- `write` – ganze Datei, nur erlaubt, wenn sie vollständig gelesen wurde
- `delete`

Edits werden vor dem Review technisch validiert (Pfad, Existenz, Eindeutigkeit von `old_text`). Probleme gehen als Korrekturrunde an den Coder zurück, nicht als Abbruch. Der Reviewer sieht den fertigen Unified Diff.

**Schreibsperre für gekürzte Dateien.** Dateien über `context_max_bytes_per_file` werden gekürzt gelesen und dürfen nicht per `write` ersetzt werden – vorher konnte der hintere Teil einer 30-KB-Datei verloren gehen. `replace` funktioniert weiterhin, weil es die Datei auf der Platte trifft, nicht den gekürzten Ausschnitt.

**Transaktion statt Backup-Runden.** Ein `WorkspaceTransaction` pro Auftrag: Snapshot beim ersten Schreibzugriff pro Pfad, über alle Reparaturrunden hinweg. Rollback stellt jeden berührten Pfad zurück und löscht in Reparaturrunden neu erzeugte Dateien. Auch bei einer Exception nach begonnenem Schreiben wird zurückgerollt (Status `error-rolled-back`). Schreiben ist atomar (Temp-Datei + `os.replace`).

**Git ohne `add -A`.** Der Git-Checkpoint vor dem Anwenden entfällt (die Transaktion ist die Sicherung). Mit `git_branch_per_task: true` wird nach grünem Test committet – nur die vom Auftrag berührten Pfade. Deine eigenen uncommitteten Änderungen bleiben unangetastet.

**Pfadschutz case-insensitiv.** `.GIT/`, `Node_Modules/` usw. sind auf Windows dieselben Verzeichnisse und werden jetzt ebenso geblockt.

**Tests als `argv`, ohne Shell:**
```json
"tests": [
  {"name": "unit", "argv": ["python", "-m", "pytest", "-q"], "timeout_sec": 300}
]
```
`test_command` (String) funktioniert weiter und wird ohne Shell gesplittet. Mehrere Profile laufen nacheinander, der erste rote stoppt.

**Testsuite:** `python -m unittest tests.test_studio -v` – 9 Tests gegen einen Mock-Ollama (replace, mehrdeutiges `old_text`, gekürzte Datei, Rollback inkl. Reparaturdatei, grüne Reparatur, `../` und `.GIT/`, Git-Scoping, argv-Tests). Läuft ohne GPU.

**Setup setzt zusätzlich** `OLLAMA_FLASH_ATTENTION=1` und `OLLAMA_KV_CACHE_TYPE=q8_0` (Ollama danach neu starten).

## 10. Bewusst nicht gebaut
- Tool-Calling des Modells (Structured Output ist für 7–30B-Modelle belegt robuster)
- Embedding-/Vektorindex (unbelegt, dass er den Symbol-/Stichwort-Scout schlägt)
- eigene GUI oder Editor (Cline/Continue/OpenCode existieren; bei Bedarf OpenAI-kompatibler Endpunkt als späterer Zusatz)
- Docker/WSL2-Sandbox (für Single-User-Workspace mit Transaktion + Pfadschutz überdimensioniert)
- weitere Agentenrollen (kein belegter Grenznutzen)

Nicht gegen echtes Ollama/NVIDIA ausgeführt. Erster realer Lauf: `python studio.py --doctor`, dann eine kleine Aufgabe mit gesetztem Testprofil.

## 11. Dateien

```text
README.md
config.json
setup.ps1
start.cmd
studio.py
workspace/
runs/
backups/
```
