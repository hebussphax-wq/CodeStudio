# Gemeinsames autonomes CodeStudio

Ein Kern für Standalone, TobyKi, HaloMonsterAI und BizDrive.
Die vorhandenen Produktfunktionen bleiben in ihren Hosts: TobyKi-Projekte,
TaskGraph, KI-Team, Ressourcen und Freigaben; Halo/BizDrive-Werkbank mit
Dokumenten, Revisionen, Exporten und Builder-Diagnose. Projektcode wird im
gemeinsamen VS-Code-Studio bearbeitet.

## Ablauf

Einmal **Autonom entwickeln** für ein Projekt und eine Aufgabe starten.
Der Auftrag plant Teilaufgaben, liest Dateien, erzeugt geprüfte Änderungen,
schreibt sie, führt die eingestellten Tests aus und repariert Fehler automatisch.
Ein abschließendes Modellreview (QC) prüft die ursprünglichen Akzeptanzkriterien.
Nur erfolgreiche Projekttests und ein positives Schlussreview ergeben
succeeded. Das ist ein Entwicklungsergebnis, keine Host-/Owner-Abnahme.

Standardgrenzen: 6 Schritte, 3 Reparaturen, 30 Minuten, 80 tatsächliche
Modellanfragen, 32 geänderte Dateien. Grenzen sind keine Leistungsgarantie
für ein bestimmtes Modell. Gescheiterte Teilschritte werden an die abschließende
Reparatur übergeben; keine unbegrenzte Wiederholung.

## Kontext und Lernen aus Fehlschlägen

Coder und Reviewer erhalten zusätzlich zum Schreibplan einen getrennten
Lesekontext: README/Anforderungen, vorhandene Tests und Scout-Treffer.
Standardbudget: insgesamt 16.000 Quellbytes, höchstens 8 Dateien und 8.000 Bytes
pro Datei. Kürzungen werden markiert. Zugangsdaten, Binärdateien und unzulässige
Pfade bleiben ausgeschlossen. Referenzen erweitern den Schreibplan nicht.
Ihre Hashes binden den Vorschlag: geänderte Anforderungen machen ihn ungültig.

Abgelehnte Änderungsvorschläge werden als lokale Fehlererfahrungen unter
`lessons/` neben `runs/` gespeichert: Änderungshash und gekürzte Diagnose.
Der Schlüssel bindet Projekt, exakten Auftrag und gelesene Ausgangsdateien.
Ein weiterer Versuch bekommt diese Diagnosen; identische bereits abgelehnte
Änderungen beenden die Vorschlagsrunde als `no_progress`, bevor dieselbe
Modellprüfung wiederholt wird. Änderungen am Ausgangsstand erzeugen einen
anderen Schlüssel. Die letzten acht Diagnosen dienen als Daten und erteilen
keine Tool- oder Schreibrechte. Dies ist nachvollziehbare Fehlererinnerung,
kein Training der Modellgewichte und keine automatische Installation von Skills.

Der DonkeyMonkey-Test hat diese Kontextlücke im vorherigen Kern nachgewiesen:
ein Plan für eine neue `game.js` enthielt nicht den Inhalt der vorhandenen
README/Testdateien. Die Regressionen in `tests/test_reference_context.py`
prüfen diese Ursache, Kontextgrenzen, Änderungsbindung und Wiederholungsschutz.

Der Auftrag besitzt eine Gesamtsicherung. Zwischenstände können Tests noch
nicht bestehen. Bei Fehlschlag, Budgetende oder Abbruch werden die eigenen
Änderungen zurückgerollt. Fremde Änderungen bleiben erhalten und erzeugen
gegebenenfalls einen Wiederherstellungskonflikt. Vorhandene Tests und das
eingestellte Testprogramm dürfen vom Modell nicht abgeschwächt werden.
Neue Testdateien dürfen entstehen. Ein fehlendes Testprogramm blockiert den Start.
Testprogramme laufen mit den Rechten des Benutzers; dies ist keine Sandbox.

**Autonomen Auftrag stoppen** unterbricht den Ablauf. Laufende Tests werden
einschließlich Prozessbaum beendet, bevor zurückgerollt wird. Bei einer laufenden
Modellanfrage wird deren Ergebnis nach Abbruch verworfen; der Modellserver kann
die bereits begonnene Berechnung noch beenden. Ein harter Prozess-/Stromausfall
ist keine automatische Wiederaufnahme: Sicherung und Lock bleiben zur Prüfung erhalten.

## Gemeinsame Workflow-/KI-Mitarbeiter-Schnittstelle

Alle Produkte verwenden denselben lokalen JSONL-Dienst:
CodeStudio.exe --serve --workspace <absoluter Projektordner> --state-dir <privater Zustand>
Optional --host-binding <projektgebundene TobyKi-Sitzungsdatei>.
Keine Modelle oder Shell-Befehle werden über einen ungeprüften Workflowtext installiert.

Zuerst configure mit context_tokens und test_argv senden und die Antwort abwarten.
Danach autonomous mit einmaliger run_id (32 Hexzeichen), approved:true, model,
limits und optionalem job senden. Durch approved bestätigt der aufrufende Host
die bereits überprüfte Ausführungsautorisierung; das Protokoll ersetzt seinen
Berechtigungsmechanismus nicht.

job:
- schema: codestudio.job.v1
- product: codestudio | tobyki | halomonsterai | bizdrive
- project_id, task_id, workflow_id, employee_id: gebundene Kennungen
- task: konkrete Entwicklungsaufgabe
- acceptance: nichtleere Liste überprüfbarer Kriterien
- execution_authorized: true nach produktgebundener Host-Prüfung

status liefert aktiven Auftrag und Fortschritt; cancel mit run_id stoppt genau
diesen Auftrag. history liefert die letzten 30 Belege dieses Projektordners.
Die autonome Endantwort enthält einen kompakten receipt sowie receipt_path.
Vollständiger Plan, Diffs, Testausgaben, Sicherungs- und Nachherhashes stehen
im atomisch geschriebenen Originalbeleg. Ereignisse und Antwort-IDs bleiben getrennt.

workflow_result (codestudio.job-result.v1) bindet dieselben Produkt-/Projekt-/
Task-/Workflow-/Mitarbeiterkennungen, Auftrags- und Originalreceipt-Hash.
workflow_next=verify_and_review bei succeeded, sonst inspect_failure.
host_accepted bleibt false. Der Host prüft Originalbeleg, Ziel, Dateihashes und
Testresultat selbst; er übernimmt keine automatische Owner-Entscheidung.

## Konsolidierungsanschlüsse

TobyKi: vorhandenen tobycode:openVscode -> core/codestudio.openEditor beibehalten.
Hostmodelltransport, Ressourcenbindung und erneute Sitzungsprüfung vor jedem
Schreiben bleiben erhalten. TaskGraph/TeamWorkflow benötigen eine ausdrücklich
autorisierte autonome Entwicklungscapability, die das gemeinsame Jobprotokoll
nutzt und das Ergebnis an verifying/awaiting_owner zurückgibt.

Halo/BizDrive: CodeStudioView ist derzeit eine HaloWerkbankView-Ansicht.
Die Werkbank und ihre sechs Dokumentformate bleiben erhalten. Der primäre
Projekt-Coding-Einstieg soll dieselbe C:/CodeStudio-Installation öffnen.
Der untersuchte Tauri-Quellstand heißt bizdrive_ai_halo; daraus folgt keine
Liveintegration aller installierten Halo-/BizDrive-Instanzen.
Vorhandene Marker-/Sandbox-Diagnosen sind keine alternative Entwicklungsengine.

Der Launcher CodeStudio.exe <Projektordner> öffnet das gemeinsame Standalone-
Studio. Für Workflow-Kopplung nutzt der jeweilige Host den obigen Dienst und
seine Autorisierung. Ein bloßer Editorstart ist keine Workflow- oder Modellbindung.
