# Konsolidierter Stand

Die ausführbaren Quellen des CodeStudio-Kerns und der VS-Code-Erweiterung entsprechen dem
TobyKi-Unterbaum bei `b7270bb3ef8133319b2161af8a06981f2e721e56`.
TobyKi enthält diesen unveränderten Unterbaum auch bei
`600ca50c7cac217c5af5f65e441ad2fa55ad3d67`, ergänzt um seine Host-Modellprüfung.
Dieses Repository ergänzt Lieferhinweise, einen Setup-Hinweis und definierte
Zeilenenden; eigene Dateien im Beispielarbeitsbereich bleiben von Git ausgeschlossen.
Lizenz und JSON-Konfiguration unterscheiden sich ausschließlich
durch die Entfernung einer zusätzlichen Leerzeile am Dateiende.

Verifiziert am 14. September 2026 unter Windows:

- 38 Python-Tests im separaten CodeStudio-Checkout bestanden.
- Installierte VSIX 0.3.1 mit enthaltener Python-Laufzeit: echter Ollama-Lauf
  mit `qwen2.5-coder:7b`, 8192 Kontext, nativer VS-Code-Diff, ausdrückliche
  Freigabe und bestandener Projekttest. Der Vorschlag änderte die Datei noch nicht.
- Gefrorener Windows-Testprozess: Testfehler, Zeitüberschreitung und
  Ausgabelimit führten jeweils zum bytegenauen Rückrollen.
- VS-Code-Installation und alle 1003 Dateien der installierten Erweiterung geprüft.

Diese Belege gelten für die geprüften Abläufe. Sie sind keine Zusage, dass
jedes lokale Modell beliebige große Entwicklungsaufgaben autonom lösen kann.
Die Bereitstellung der gesamten TobyKi-App wird getrennt abgenommen;
HaloMonsterAI ist hier noch nicht integriert.

Das Quellpaket startet mit Python 3.11+ über `python codestudio.py`.
Die Erweiterung benötigt keine JavaScript-Abhängigkeiten oder Übersetzung.
Das separate Windows-Paket enthält den gefrorenen Python-Dienst und die
installierte VSIX. VS Code und Ollama bleiben eigenständige Voraussetzungen.
`setup.ps1` verändert keine globalen Ollama-Einstellungen und lädt keine Modelle.
