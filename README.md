# CodeStudio

Lokales Coding-Studio in **Visual Studio Code**, mit Scout, Planer, Coder,
Reviewer, nativem Diffeditor, Freigabe, Projekttests und Rückrollen bei Fehlern.

## Autonome Entwicklung

**Autonom entwickeln** plant und bearbeitet einen Auftrag über mehrere Dateien, führt
die eingestellten Projekttests aus und repariert Fehler selbstständig. Ein Start
autorisiert diesen begrenzten Auftrag; einzelne Diffs brauchen dann keine weitere
Freigabe. **Stoppen** beendet den Auftrag und rollt eigene Änderungen zurück.
[Funktionsumfang, Grenzen und gemeinsame Workflow-Anbindung](AUTONOMOUS.md).

## Start

Voraussetzungen: Visual Studio Code, Ollama und ein installiertes Coding-Modell.
Im vollständigen VS-Code-Windows-Paket startet `CodeStudio.exe` einen eigenen
VS-Code-Arbeitsbereich mit der CodeStudio-Erweiterung und enthaltener Python-Laufzeit.
Das frühere Tkinter-Paket enthält diese Erweiterung noch nicht.
Im Quellpaket startet `python codestudio.py [Projektordner]` (Python 3.11+)
VS Code mit der Quellerweiterung; alternativ `start.cmd` öffnen.

1. Lokalen Projektordner in VS Code öffnen und die CodeStudio-Seitenleiste wählen.
2. Unter „Modell“ ein installiertes Ollama-Modell und unter „Kontext“ die passende Größe auswählen.
3. Unter „Projekttests einstellen“ eine ausführbare Argumentliste festlegen oder unter
   „Testprofile laden“ eine JSON-Datei mit mehreren benannten Profilen öffnen.
   Jedes Profil enthält `name`, `argv` und `timeout_sec`; alle laufen in der Schlussprüfung.
   Gespeicherte `codestudio.testProfiles` gelten auch für den normalen Testknopf und
   den freien autonomen Auftrag, ohne vorbereiteten Modul-Workflow.
   `codestudio.testCommand` bleibt als einzelnes Testprogramm unterstützt.
4. Für einen autonomen Auftrag „Autonom entwickeln“ wählen. „Laufgrenzen einstellen“
   öffnet die Einstellungen für Schritte, Reparaturen, Minuten, Modellaufrufe, Kontext
   und Ausgabetokens. Das Aufrufbudget umfasst auch Reparaturen und Ersatzmodelle.
   Größere Ausgabegrenzen benötigen gemeinsam mit der Eingabe genügend Kontext.
   Alternativ „Aufgabe planen und Diff erzeugen“ wählen und Aufgabe mit Akzeptanzkriterien eingeben.
5. Vorschlag prüfen, dann „Geprüften Diff anwenden“ bestätigen.

Bei fehlgeschlagenen Tests wird bytegenau zurückgerollt. Der Testfehler steht
unter „Ablauf und Belege“ und kann einer neuen Aufgabe beigefügt werden.
Auch der nächste Vorschlag benötigt eine Diff-Freigabe. Ohne konfigurierte Projekttests wird
das Ergebnis ausdrücklich als ungeprüft angezeigt.

Das eigenständige VS-Code-Profil liegt unter `%LOCALAPPDATA%/CodeStudio/VSCode`.
Projekteinstellungen liegen in den VS-Code-Arbeitsbereichseinstellungen;
Laufbelege und Sicherungen im globalen Erweiterungsspeicher dieses Profils,
getrennt nach Projekt. „Ablauf und Belege“ zeigt den konkreten Belegpfad.
Ungespeicherte Editoränderungen blockieren Generierung und Anwendung.
`python codestudio.py --doctor` prüft die Ollama-Verbindung.
Die frühere Tkinter-Oberfläche bleibt ausschließlich unter `--legacy-gui` verfügbar.

## Gemeinsamer Kern und TobyKi

`core.py`, `safety.py` und `processrunner.py` bilden den gemeinsamen Kern.
`vscode/` enthält die VS-Code-Erweiterung, `service.py` ihren lokalen Dienst,
`codestudio.py` den Startpunkt. `bridge.py` erzeugt ausschließlich
Vorschläge für Host-Anwendungen. Das Eingabeschema ist `codestudio.propose.v1`,
die Antwort `codestudio.proposal.v1`; Transport: JSON über stdin/stdout.

TobyKi verwendet `src/core/codestudio.js` und die bestehende TobyCode-Patchansicht.
Projekt-/Taskbindung und aktuelle Dateihashes werden geprüft. Die weitere
Sandbox-, Test-, Freigabe- und Übernahmelogik bleibt bei TobyKi. Der Adapter
benötigt den Kernordner in der jeweiligen TobyKi-Installation und Python 3.11+.
`CODESTUDIO_PYTHON` kann einen absoluten Interpreterpfad vorgeben.
„In VS Code öffnen“ übergibt den gebundenen Projektordner an das installierte
CodeStudio-Paket. Dieses wird standardmäßig neben TobyKi als `CodeStudio`
erwartet; `CODESTUDIO_HOME` kann den Installationsordner vorgeben.
Eine erfolgreiche Startübergabe bestätigt noch keine sichtbare Editorwirkung.

Beide TobyKi-Einstiege verwenden dessen Main-Modelltransport. Vor jedem
tatsächlichen Modellaufruf werden die aktuelle Hostkonfiguration, das exakt
installierte Modell und die Ressourcen erneut geprüft. Kontext und CPU-/GPU-
Optionen kommen dabei aus TobyKi, nicht aus den Standalone-Defaults.
Die Vorschlagsbrücke verwendet gebundene JSONL-Modellaufrufe; VS Code erhält
eine lokale, auf ein Projekt begrenzte Sitzung für Modelle und Chat.
Die Sitzung endet nach 30 Minuten oder beim Beenden von TobyKi. Ungültige,
abgebrochene oder veränderte Bindungen führen zu einem Fehler und niemals
zum stillen Wechsel auf Port 11434. Ein offener Vorschlag wird vor Anwendung
erneut gegen die Hostsitzung geprüft. Für eine neue Sitzung das Projekt
erneut aus TobyKi öffnen. Die Sitzungsdatei bleibt im privaten TobyKi-Datenordner;
Projektdateien und Standalone-Konfiguration werden dadurch nicht geändert.

Ein Installationsbeleg `CodeStudio-install.json` bindet Erweiterungsordner,
Erweiterungsversion und SHA-256 der ausgelieferten Dateien. Vor dem Start
prüft TobyKi diese Dateien sowie VS-Code-Installation und Projektpfad erneut.
Dieser Beleg wird erst nach tatsächlicher VSIX-Installation erzeugt.

HaloMonsterAI kann später denselben Vorschlagsadapter verwenden. Seine
historischen Beispiel-Patches und Marker-Tests werden nicht als Projektengine
übernommen. Eine heutige Halo-Integration oder TobyKi-Live-Installation ist
mit dem Vorhandensein dieser Quellen nicht behauptet.

## Schutz und Grenzen

Workspace- und Dateihashes verhindern veraltete Anwendung; Vorschläge sind
nicht wiederverwendbar. Links, geschützte Ordner und typische Zugangsdatenpfade
sind ausgeschlossen. Mutmaßliche Zugangsdaten im Text werden vor Modellkontext
blockiert; dies ist eine Musterprüfung, keine Garantie für beliebige Geheimnisse.
Dateien müssen UTF-8-Text sein; vorhandene Zeilenenden bleiben erhalten.

Testprogramme sind vom Benutzer gewählte ausführbare Programme. Sie besitzen
dessen Dateirechte; der Testprozess ist keine Sicherheits-Sandbox. Unter Windows
werden Tests samt Nachkommen in einem Job kontrolliert, ihre Ausgabe begrenzt
und der Prozessbaum vor Rückrollen beendet. Ein nicht bestätigtes Prozessende
bleibt gesperrt und verlangt manuelle Prüfung der Sicherung. Gleichzeitige
Bearbeitung durch fremde Programme kann zu einem bewusst erhaltenen Konflikt
führen. Bei einem harten Programm-/Stromausfall bleiben Sicherungsmanifest und
gegebenenfalls Workspace-Lock erhalten; eine automatische Neustart-Recovery
wird nicht behauptet.

## Tests

`python -m unittest discover -s tests -v` im CodeStudio-Ordner.
TobyKi: `node tests/run_all_tests.js` im TobyKi-Quellverzeichnis.
Testbelege gelten für ihre jeweilige Quellidentität und Testumgebung.

## Herkunft

Ausgangspunkt: `hebussphax-wq/CodeStudio`, Commit
`0fc7b7119439ddb27e7d44b9ae72867f15bcea28` vom 14. September 2026.
Die MIT-Lizenz des Ausgangspakets bleibt als `LICENSE` enthalten.
Konsolidierung ohne Änderung der TobyKi-Produktversion.

## Lokales Entwicklungsbeispiel

[Donkey Monkey](examples/donkey-monkey/README.md) enthält das durch lokale
CodeStudio-Modelläufe erzeugte Vier-Level-Spiel, den mit ComfyUI erzeugten
Hintergrund, ausführbare Prüfungen und eine getrennte Dokumentation der
autonomen Arbeitsschritte und der betreuten Abnahme.
