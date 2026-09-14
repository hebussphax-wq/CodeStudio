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

In VS Code ergänzt die Erweiterung den Auftrag um das gewählte lokale Projekt,
die aktive Datei und Zeile, eine begrenzte Auswahl sowie Fehler/Warnungen der
Sprachdienste. Andere Arbeitsbereiche und geschützte Pfade werden ausgefiltert.
Die Ansicht bietet Dateinavigation, die native Probleme-Ansicht, eine lesbare
Kontextvorschau und das explizite Ausführen der konfigurierten Projekttests.
Ungespeicherte Änderungen verhindern die Arbeit am abweichenden Plattenstand.
Native Diffs und die vorhandene Freigabe/Rückrolllogik bleiben maßgeblich.
Editor-Diagnosen sind Beobachtungen; sie ersetzen keine ausgeführten Tests.
Das Modell erhält keine beliebige VS-Code-Befehls- oder Terminalausführung.

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
# Vorhandene lokale Modellpools

In der VS-Code-Seitenleiste kann ein eigenständiges CodeStudio über **Ollama**
die Adresse eines bereits laufenden lokalen Dienstes auswählen. Danach zeigt
**Modell** dessen tatsächlich verfügbare Modelle. Die Adresse wird pro Projekt
gespeichert; Modellwechsel oder Adresswechsel verwerfen alte Vorschläge.

Ein Modellordner auf E: oder H: muss vom gewählten Ollama-Dienst bereitgestellt
werden. Eine Datei auf einem Laufwerk allein beweist weder ein ladbares Modell
noch eine bestandene Coding-Aufgabe. CodeStudio lädt über diese Auswahl keine
Modelle herunter und ändert keine globalen Ollama-Einstellungen. Unterstützt
werden lokale HTTP-Adressen (localhost, 127.0.0.1, ::1). Hostgebundene TobyKi-
Projekte behalten ihre Hoststeuerung und erlauben hier keine Adressüberschreibung.

## Modulabläufe und lokale Grafiken

Ein expliziter Workflow (schema codestudio.modules.v1) ergänzt den bisherigen freien Plan. Jedes Modul enthält id, contract, files (höchstens vier), references, depends_on, tests (Indizes freigegebener Testprofile) und optional models mit coder/reviewer. Nur diese Dateien sind schreibbar. Ein Modul wird erst nach Review und kumulativen Tests als verified vermerkt. Fehler werden innerhalb des Reparaturbudgets sofort bearbeitet; abhängige Schritte beginnen erst danach. Zum Schluss laufen alle Profile. Bei Fehlschlag wird weiterhin der gesamte Lauf zurückgerollt. Automatische Wiederaufnahme ist noch nicht implementiert.

VS Code: „Modul-Workflow laden“ liest eine JSON-Datei mit task, workflow und test_profiles. Der geöffnete Vertrag samt Programmen wird vor Start geprüft und freigegeben. Programme bleiben vom Auftraggeber definiert; das Modell kann keine Shell-Befehle auswählen. Ein Workflow beweist nur seine deklarierten Kriterien, nicht die Vollständigkeit jeder unklar formulierten Produktidee.

„Grafik lokal mit ComfyUI erzeugen“ verwendet einen bereits laufenden lokalen ComfyUI-Dienst (Standard 127.0.0.1:8189), vorhandenen Checkpoint und ausschließlich Standardknoten. Es lädt keine Modelle oder Erweiterungen herunter. Der Auftrag wird mit Prompt-ID protokolliert. Erst fertige History, PNG-Prüfsummen, passende Abmessungen und zurückgelesener Datei-Hash ergeben succeeded. Nur neue PNG-Pfade im Projekt werden importiert. Bei unklarer Übermittlung wird nicht automatisch neu eingereiht. Grafiken sind getrennte Aufträge und werden bei einem späteren Code-Fehlschlag nicht entfernt. Hostgebundene Grafikerzeugung benötigt noch einen eigenen Hostvertrag.

Lokale Coder und ComfyUI teilen Grafikspeicher: schwere Generierungen nacheinander ausführen. Das Vorhandensein eines Modells/Studios beweist weder Anbindung noch erfolgreiche Entwicklung.

Im Modulmodus erfolgt zuerst die begrenzte Anwendung innerhalb der Rückrolltransaktion, dann der reale kumulative Test und erst bei bestandenem Test das Modellreview. Dadurch bewertet der Reviewer konkrete Ergebnisse. Kein Modul wird vor beidem als verified markiert. Der freie Diff-/Freigabemodus behält sein Review vor Anwendung.

Modul-Coder liefern pro Datei nur path, op und vollständigen content; die separaten old_text/new_text-Felder des freien Patch-Modus entfallen. Bereits installierte lokale Werkzeugprofile können optional unter LOCALAPPDATA/CodeStudio/local-tools.json registriert werden. Der Standalone-Dienst startet ausschließlich passende, aktivierte lokale Profile mit direkten Argumentlisten, prüft ihre API und protokolliert den Prozess. Bei unklarem Start bleibt eine Startsperre erhalten. Keine Modell-Downloads oder globale Dienständerungen.

Module QC displays source with real line breaks. A model rejection after passing tests receives one bounded countercheck against the same source and contract; both judgments remain in the receipt. A confirmed rejection still blocks dependents and rolls back. This handles disputed model findings without treating test success alone as completion.

QC now receives all module files and bounded read-only references. Factual observations precede the verdict, and a review rejection remains in repair feedback after a no-op. Host and direct coders share the same module prompt/schema. Model metadata must be interpreted with the active Ollama renderer. A visible `{{ .Prompt }}` template alone does not prove a broken chat setup: built-in renderers can take precedence. The local template-alias experiment did not establish a repair.

After a failed module test, the planner diagnoses the actual source and test output before the coder attempts repair. The bounded diagnosis is cached by exact module source and failure output, so an unchanged no-op reuses the diagnosis instead of spending another analysis call. Diagnostics never grant new write paths or test-edit permission.

The final module review includes unchanged declared module files and read-only dependencies, not only files written in the current run. Its complete file identities are recorded; count, per-file and total context limits fail closed instead of silently dropping dependencies.
