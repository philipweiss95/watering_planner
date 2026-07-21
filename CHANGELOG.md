# Changelog

Alle stabilen Änderungen am Watering Planner werden in dieser Datei dokumentiert.

## [1.1.0] - 2026-07-21

- Einstellungen, Schläuche, Pflanzen und Info als einheitliche, übersichtlichere Arbeitsbereiche neu gestaltet.
- Mobile Navigation als gut erreichbare Bottom-Bar mit größeren Touch-Zielen umgesetzt.
- Einstellungsbereiche mit Abschnittsnavigation, Nummerierung und hervorgehobener Speicheraktion vereinfacht.
- Schlauchzuordnung und Pflanzenkarten mit klaren Statusanzeigen, Hilfetexten und leeren Zuständen verbessert.
- Updater, Changelog und Shortcuts auf der Info-Seite neu gegliedert.
- Responsive Layouts für Smartphone, Tablet und Desktop sowie Fokus- und Reduced-Motion-Zustände ergänzt.

## [1.0.3] - 2026-07-21

- Synology-Updater erkennt die tatsächliche Compose-Projektkennung der laufenden Container aus deren Docker-Labels.
- Konflikte mit den fest benannten Containern `watering-planner` und `watering-planner-updater` beim Update und beim Rollback behoben.

## [1.0.2] - 2026-07-21

- Updater aus den Einstellungen auf die Info-Seite verschoben.
- Dauerhaft gespeicherten GitHub-Token in der Oberfläche eindeutig gekennzeichnet; der Token selbst wird weiterhin nie an den Browser zurückgegeben.
- Versionsbezogene Changelogs für Update-Prüfung und abgeschlossene Installationen ergänzt.
- Release-Workflow veröffentlicht den passenden Changelog-Abschnitt als GitHub-Release-Text.

## [1.0.1] - 2026-07-21

- Automatische Nachfüllmenge korrigiert: Um 01:00 und 06:00 werden jeweils 50 Prozent der zu diesem Zeitpunkt fehlenden Haupttankmenge nachgefüllt.
- Nachfüllberechnung vom Verbrauch des Vortags entkoppelt.

## [1.0.0] - 2026-07-21

- Eichung des Haupttankverbrauchs über manuelle Füllstandsmessungen und einen separaten Verbrauchsfaktor eingeführt.
- Eichung der Nachfüllpumpe über Vorratstankmessung und protokollierte Pumpzeit ergänzt.
- Deaktivierbare Nachfüllautomatik mit festen Zeitfenstern um 01:00 und 06:00 eingeführt.
- Synology-Updater mit stabilem GitHub-Kanal, SHA-256-Prüfung, Backup und Rollback ergänzt.
