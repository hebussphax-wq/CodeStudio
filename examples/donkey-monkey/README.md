# Donkey Monkey

Eigenes kleines Dschungel-Arcadespiel mit vier Levels. Öffne `index.html` in einem aktuellen Browser; das Spiel benötigt weder Internet noch Ollama oder ComfyUI zur Laufzeit.

- Links/rechts: laufen
- Rauf/runter: Leitern
- Leertaste: springen
- Startknopf oder Enter: starten/neustarten

Sammle Bananen, weiche den rollenden Fässern aus und erreiche die Fahne oben rechts. Nach dem vierten Ausgang ist das Spiel gewonnen.

## Herkunft und tatsächliche Autonomie

Die Spielimplementierung wurde mit dem lokal vorhandenen `qwen3.6:27b` über CodeStudio erzeugt und repariert. Der Hintergrund `assets/jungle.png` wurde lokal mit ComfyUI und dem vorhandenen SDXL-Basismodell erzeugt. Es wurden keine Modellgewichte heruntergeladen und keine Cloudmodelle für den Spielcode verwendet.

Aufgabenvertrag, Modulaufteilung, Tests und externe Abnahme stammen vom betreuenden Agenten. Mehrere Fehlversuche wurden zurückgerollt; bereits geprüfte Modell-Ausgaben wurden bytegleich als Ausgangspunkt weiterer begrenzter Läufe übernommen. Das ist ein betreuter Entwicklungsnachweis, kein Nachweis eines vollständig unbeaufsichtigten einzigen Auftrags. Automatische Wiederaufnahme solcher Zwischenstände ist noch nicht implementiert.

## Prüfen

Node.js erforderlich, keine npm-Pakete:

```text
node tests/run_all.cjs
```

Die Modulverträge in `donkey-monkey.workflow.json` erklären die erzeugte Aufteilung. Ein erneuter Modelllauf ist stochastisch und kann von diesem geprüften Ergebnis abweichen.

## Nachweise

`provenance.json` bindet Dateien per SHA-256 an Modellläufe und Grafikbeleg. Drei vollständige Eingabe-Simulationen erreichten alle vier Levels mit drei Leben; dabei wurden weder Positionen versetzt noch Hindernisse entfernt. Browserdarstellung und echte Tastatureingaben werden getrennt dokumentiert.

Der vollständige Tastaturtest lädt die unveränderten Browserskripte in eine isolierte DOM-/Canvas-Testumgebung. Ein vorausschauender Testcontroller betätigt ausschließlich die registrierten Tastenhandler; Zustand und Hindernisse werden nicht manipuliert. Seine Vorausschau verwendet dieselben variablen Zeitschritte wie die tatsächliche Animationsschleife. Das ersetzt keine Prüfung der Grafik im echten Browser.
