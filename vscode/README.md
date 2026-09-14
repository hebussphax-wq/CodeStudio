# CodeStudio in Visual Studio Code

CodeStudio verbindet den VS-Code-Editor mit dem lokalen Ollama-Coding-Kern.
Projekt öffnen, in der CodeStudio-Seitenleiste Modell, Kontext und Projekttests
einstellen und eine Aufgabe beschreiben. Änderungen erscheinen im nativen
Diffeditor und werden erst nach Bestätigung angewendet. Fehlgeschlagene
Projekttests lösen ein Rückrollen aus; ohne Testprogramm bleibt das Ergebnis
ausdrücklich ungeprüft.

Das vollständige Windows-Paket enthält die lokale Laufzeit. Die Quellerweiterung
benötigt einen absoluten Python-3.11+-Pfad über `codestudio.pythonPath` oder
`CODESTUDIO_PYTHON`. Ollama und das gewählte Modell müssen lokal installiert sein.
Die Erweiterung benötigt einen vertrauenswürdigen lokalen Arbeitsbereich.
Testprogramme laufen mit den Rechten des angemeldeten Benutzers.
