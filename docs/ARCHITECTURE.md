# Betriebsrelevante Architekturentscheidungen

Die vollständige Modulübersicht steht in
[backend-architecture.md](backend-architecture.md). Dieses Dokument hält die
für Betrieb und Migration wichtigen Garantien der Version 1.5.0 fest.

## Persistente Nachfüllfenster

`RefillService` legt beim Anwendungsstart und bei jeder Statusauswertung die
konfigurierten Fenster für heute und morgen in `refill_window_plans` an. Ein
Plan speichert:

- lokales Zieldatum und sichtbare Fensterkennung,
- Start und Ende als absolute UTC-Zeitpunkte,
- festes `window_key`,
- festgestellten Bedarf und technische Ausführbarkeit,
- erwartete Transfermenge,
- Erstellungs- und letzten Prüfzeitpunkt,
- Beobachtung im offenen Fenster, Erfüllung oder Stornierung.

Der Unique-Schlüssel und ein SQLite-Upsert machen die Planung idempotent. Ein
passendes `refill_event` erfüllt den Plan. Erst nach dem gespeicherten
Fensterende wird ein benötigter und ausführbarer, aber nicht erfüllter Plan als
`window_missed` bewertet. Dadurch bleibt die Erkennung auch erhalten, wenn der
Worker während des gesamten Fensters nicht lief oder der Container danach neu
startet.

Tankbuchungen aktualisieren die noch offenen Pläne unmittelbar. Dabei gewinnt
atomar nur ein fachlich neuerer, über `last_checked_at` geordneter Snapshot.
Ein vor dem Fenster gefüllter Haupttank entfernt deshalb den zuvor erkannten
Bedarf; eine verspätete parallele Statusberechnung kann ihn nicht erneut
setzen.

Die Buchung eines Nachfülllaufs und die Erfüllung der passenden Erwartung
erfolgen in derselben `BEGIN IMMEDIATE`-Transaktion. Auch durch den Lauf
vollständig vom Cooldown blockierte Folgefenster werden dort aktualisiert.
Ein Prozessabbruch direkt nach dem Commit kann deshalb nicht nachträglich
eine falsche `window_missed`-Warnung erzeugen.

Konfigurationsänderungen stornieren nur zukünftige, noch nicht begonnene
Pläne. Historische Beobachtungen werden nicht nachträglich umgedeutet. UTC-
Grenzen halten die Schlüssel bei Sommer- und Winterzeit eindeutig.

Frühe 1.5-Daten aus `refill_window_observations` werden additiv und
wiederholbar übernommen. Schema-Version 4 verändert keine vorhandenen
Bewässerungs-, Nachfüll-, Pflanzen- oder Tankdaten.

## Worker und Benachrichtigungen

Das Workerintervall ist auf 10 bis 3600 Sekunden begrenzt. Die Fensterplanung
ist davon unabhängig. Für einen leeren Vorratstank beziehungsweise ein
verpasstes Fenster wird nur die spezifische Ursache aktiv; die generische
Blockadewarnung bleibt dann inaktiv. Persistente Condition-Keys haben das
Format `refill_run_missed:<Datum>:<Fenster>`.

Bereits gespeicherte Werte früherer Versionen zwischen 1 und 86400 Sekunden
bleiben migrationsfähig. Beim ersten Lesen werden sie auf den nächsten
zulässigen Wert zwischen 10 und 3600 Sekunden begrenzt und normalisiert
gespeichert. Neue API-Eingaben außerhalb von 10 bis 3600 bleiben ungültig.

## Prognose und Wetterstatus

Die Backend-Reichweite simuliert weiterhin bis zu 45 Tage. Das Frontend
schneidet ausschließlich seine Darstellung auf die ersten 16 lokalen
Kalendertage zu, bevor SVG-Skalierung und Markierungen entstehen.

Frontend-Refreshes laufen serialisiert. Nach Wetterauswertung oder erzwungenem
Abruf werden erfolgreicher Zeitpunkt, Datenalter, Fehler, Cachetreffer,
Fallback und Veraltungsstatus gemeinsam in den Store übernommen. Ein
erzwungener Abruf verwendet
`GET /api/weather?force=true&evaluate=true&slot=morning`. Wetterstatus und die
mit genau diesen Daten berechnete Auswertung werden gemeinsam übernommen; es
entsteht kein zweiter Open-Meteo-Netzabruf. Nach einem fehlgeschlagenen Abruf
verhindert eine kurze interne Retry-Sperre standardmäßig weitere minütliche
Netzaufrufe. Die fachliche Schwelle für veraltete Wetterdaten bleibt davon
unabhängig.
