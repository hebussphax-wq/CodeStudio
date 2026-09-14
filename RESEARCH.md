# Recherche und konkrete Entscheidungen – 14.09.2026

Ausgangsbefund: Der Donkey-Monkey-Lauf mit Devstral Small 2 24B überschritt 40 Minuten nach 13 Modellaufrufen. Drei von sieben Tests bestanden; Schritte wurden trotz Fehlern weitergeführt. Der Lauf wurde zurückgerollt. Ein stärkeres Modell alleine löst dieses Ablaufproblem nicht.

- [Anthropic: Effective agents](https://www.anthropic.com/engineering/building-effective-agents): kleine verkettete Aufgaben und programmatische Zwischenprüfungen. Umsetzung: Modulvertrag, feste Schreib-/Lesepfade, kumulative Tests vor nächstem Modul.
- [Aider: Chat modes](https://aider.chat/docs/usage/modes.html): Architektur und Editierung können getrennte Rollen/Modelle nutzen. Umsetzung: Rollenmodelle pro Modul. Aider selbst ist dadurch noch nicht angebunden.
- [Anthropic: Long-running harnesses](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents): vollständige Featureliste, inkrementelle Arbeit und überprüfbare Übergaben. Noch offen: automatisch entworfene Modulverträge und konfliktgeprüfte Wiederaufnahme.
- [Ollama: Chat API](https://docs.ollama.com/api/chat): strukturierte Ausgabe, think und Laufzeit-/Tokenmesswerte. Umsetzung: explizite Thinking-Konfiguration, messbare Modellaufrufe und Abbruch bei abgeschnittener Ausgabe. Keine Modellgedanken werden gespeichert.
- [ComfyUI: lokale API](https://docs.comfy.org/development/comfyui-server/comms_routes): Prompt einreichen, History verfolgen, Bild abholen. Umsetzung: offline Standardknoten-Workflow, vorhandene Checkpoints, geprüfter PNG-Import.

Webseiten und Foren liefern Belege, keine Ausführungsrechte. Ein allgemeiner Web-Rechercheagent und weitere externe Coding-Harness-Adapter sind noch nicht implementiert. Für diese Aufgabe wurden offizielle Primärquellen verwendet. Kein Projektcode wurde in Entwicklerforen veröffentlicht.
