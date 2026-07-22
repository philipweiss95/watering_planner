# Changelog

Alle stabilen Änderungen am Watering Planner werden in dieser Datei dokumentiert.

## [1.4.1] - 2026-07-22

- Docker-Image-Tags für Planner und Updater mit der Release-Version synchronisiert.
- Release-Konsistenztest ergänzt, der Versionsdatei, Compose-Images und Web-App-Assets gemeinsam prüft.
- Automatisches Update kann den neuen Updater dadurch wieder eindeutig finden und sicher übernehmen.

## [1.4.0] - 2026-07-22

- Einstellungen auf eine klare Abfolge für Gießmenge, Balkon, Tanks und Pumpen reduziert.
- Berechnung, Beschattung und technische Ausgänge als optionale Detailbereiche eingeordnet.
- Feldbezeichnungen, Einheiten und Hilfetexte in allen Arbeitsbereichen vereinheitlicht und gekürzt.
- Speicherstatus für offene, erfolgreiche und fehlgeschlagene Änderungen ergänzt.
- Einstellungsnavigation und direkte Bereichslinks korrigiert; technische Bereiche werden beim Anspringen automatisch geöffnet.
- Statuskarten und Einstellungsbereiche für kleine Displays kompakter angeordnet.

## [1.3.3] - 2026-07-22

- Zwei getrennte nächtliche Nachfüllfenster von 01:00 bis 02:00 und 06:00 bis 07:00 aktiviert.
- Jedes Fenster kann höchstens einen automatischen Nachfüllvorgang auslösen und wird außerhalb seines Zeitraums nicht nachgeholt.
- Zwischen sämtlichen Nachfüllvorgängen wird eine Mindestpause von drei Stunden eingehalten.
- Home-Assistant-Vorlage auf beide Nachtfenster begrenzt.

## [1.3.2] - 2026-07-22

- Automatische Nachfüllung auf genau ein tägliches Nachtfenster von 01:00 bis 02:00 begrenzt.
- Das bisherige zweite Fenster um 06:00 entfernt, das nach einer Bewässerung einen weiteren halbierten Nachfülllauf ausgelöst hatte.
- Verpasste Nachtfenster werden morgens und tagsüber nicht mehr nachgeholt.
- Home-Assistant-Vorlage zusätzlich mit derselben Zeitbegrenzung abgesichert.

## [1.3.1] - 2026-07-22

- Updater-Übergabe wartet nun auf das neue Container-Image und einen erfolgreichen Healthcheck, bevor das Update als abgeschlossen gilt.
- Nach erfolgreicher Übergabe bleibt exakt ein Updater-Container des Compose-Projekts bestehen; alte, gestoppte oder noch laufende Duplikate werden entfernt.
- Der Updater gleicht seine Containeridentität beim Start selbst ab, räumt unterbrochene Ersetzungen auf und übernimmt bei Bedarf wieder den kanonischen Namen `watering-planner-updater`.
- Die unabhängige Übergabe versucht die Neuerstellung bis zu dreimal und speichert einen Fehlerstatus, falls Image-, Health- oder Eindeutigkeitsprüfung fehlschlagen.

## [1.3.0] - 2026-07-22

- Updater-Neustart an einen unabhängigen Hilfscontainer übergeben, damit die eigene Neuerstellung den laufenden Update-Prozess nicht mehr abbricht.
- Gestoppte Updater-Duplikate desselben Compose-Projekts werden vor der Übergabe gezielt entfernt.
- iPhone-Web-App-Unterstützung mit Manifest, Standalone-Modus, Safe-Area-Layout, Service Worker und eigenem Gießplaner-App-Icon ergänzt.
- Einstellungsbereiche nutzen auf großen Bildschirmen wieder die volle Breite; Abschnitts- und Seitenicons exakt zentriert.
- Pflanzenkarten auf Kernwerte verdichtet und Versorgungsdetails bedarfsgerecht aufklappbar gemacht.
- Pflanzenanlage, Schlauchzeilen und Anschlussprüfung kompakter gestaltet, ohne Bearbeitungsfunktionen zu entfernen.

## [1.2.0] - 2026-07-22

- Dashboard auf die aktuelle Bewässerungsentscheidung, den Tagesplan, beide Tankstände, Reichweite, Wetter und Systemstatus verdichtet.
- Visuelle Hierarchie, Navigation, Aktionsflächen, Statuskarten und das mobile Layout weiter vereinheitlicht.
- Vorgangsprotokoll auf die jüngsten Einträge begrenzt, damit die Übersicht schnell erfassbar bleibt.
- Pumpeneichung in eine verständlich erklärte Kalibrierung mit prozentualen Füllständen von 0 bis 100 Prozent umgestellt.
- Technische Pumpenwerte in einen optionalen Detailbereich verschoben.

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
