# Betriebsrelevante Architekturentscheidungen

Die vollständige Modulübersicht steht in
[backend-architecture.md](backend-architecture.md). Dieses Dokument hält die
für Betrieb und Migration wichtigen Garantien der Versionen 1.5.x fest.

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
wiederholbar übernommen. Schema-Version 7 verändert keine vorhandenen
Bewässerungs-, Nachfüll-, Pflanzen- oder Tankdaten.

## Persistente Nachfüllläufe

Ein physischer Nachfülllauf besteht aus zwei persistenten Phasen:

1. `RefillRunService.start()` öffnet `BEGIN IMMEDIATE`, prüft `run_id`,
   Automatik, Fenster, Cooldown, aktive Läufe, Tankplatz, Vorrat und
   Pumpendurchsatz und schreibt eine Reservierung in `refill_runs`.
2. Bei automatischen Läufen begrenzen Fensterende minus zehn Sekunden
   Sicherheitsreserve und der Pumpendurchsatz die freigegebene Menge.
3. Home Assistant claimt vor dem Einschalten atomar den Übergang
   `reserved -> running`. Nur diese erste Antwort enthält
   `pump_start_authorized=true` und eine nutzbare Dauer. Wiederholte oder
   verspätete Claims sowie alle terminalen Läufe autorisieren keine Pumpe.
4. Nach dem sicheren Ausschalten meldet Home Assistant dieselbe `run_id` an
   `/api/refill/complete`.
5. Der Abschluss verwendet ausschließlich die reservierte Menge und Dauer.
   Fensterende und später geänderte Konfiguration werden nicht neu geprüft.
6. Tankbilanz, `refill_event`, Abschlussstatus und Erfüllung des exakten
   Fensterplans werden gemeinsam unter `BEGIN IMMEDIATE` gespeichert.

Die freigegebene Pumpmenge, die anhand des aktuellen Vorrats physisch mögliche
Menge und die im Haupttank noch bilanzierbare Menge bleiben getrennt sichtbar.
Der Haupttank überschreitet nie seine Kapazität, der Vorrat wird nie negativ.
Jede Differenz erzeugt einen persistenten Konsistenzhinweis und verlangt eine
manuelle Tankprüfung.

Reservierungen laufen 15 Minuten nach dem erwarteten Abschluss aus und geben
den exklusiven Startplatz frei. Sie werden nicht als sicher „nicht gelaufen“
behandelt: `expired`, ein fehlgeschlagener bereits laufender Vorgang und eine
unvollständige Buchung bleiben in der Diagnose sichtbar. Ein verspäteter
Abschluss einer abgelaufenen, nachweislich gestarteten Reservierung ist
weiterhin möglich und idempotent, solange kein neuerer Lauf existiert. Zwei
parallele Starts und Claims werden über `BEGIN IMMEDIATE` und den
partiellen Unique-Index auf `active_slot` serialisiert; die `run_id` und der
Unique-Index von `refill_events` verhindern doppelte Abschlussbuchungen.

Unklare Läufe sperren Reservierung und Claim weiterer Läufe. Der Endpunkt
`POST /api/refill/runs/{run_id}/reconcile` löst sie mit `no_transfer`,
`full_transfer`, `measured_transfer`, `tank_levels_corrected` oder
`cancelled_after_review` atomar auf. Abgleichart, Zeitpunkt und Notiz werden
in `refill_runs` gespeichert. Schema 7 ergänzt dazu die normalisierte
Abgleichanfrage. Nur eine inhaltlich identische Wiederholung ist idempotent;
abweichende Modi, Mengen oder Tankstände werden als Konflikt abgelehnt.
Existiert bereits ein physisches
`refill_event`, bleibt es unverändert; dann sind nur Tankstandkorrektur oder
die dokumentierte Prüfbestätigung zulässig.

Die Home-Assistant-Vorlage merkt die aktive Kennung in `input_text`, startet
einen restaurierbaren Sicherheitstimer und schaltet die Pumpe sowohl beim
Timerablauf als auch bei einem Home-Assistant-Neustart aus. Ein Neustart kann
die bis dahin tatsächlich übertragene Teilmenge nicht rekonstruieren; der Lauf
wird deshalb als unklar gemeldet und muss anhand der Tankstände geprüft werden.
Fehlt direkt nach `switch.turn_on` die Einschaltbestätigung, wird unverzüglich
ausgeschaltet und der mögliche Teiltransfer gemeldet. Timer und aktive Kennung
werden erst nach bestätigtem `off` gelöscht. Bleibt der Zustand unklar, startet
der Sicherheitswächter erneut und behält die Kennung für den nächsten Versuch.
Dasselbe gilt nach einem Home-Assistant-Neustart: Wird `off` nicht bestätigt,
wird der Guard ausdrücklich für weitere 15 Sekunden gestartet und die aktive
`run_id` bleibt bis zu einer später bestätigten Abschaltung erhalten.

## Atomare Konfiguration

`HosesRepository.save()` validiert die vollständige Schlauchliste, alle
Fremdschlüssel und Anschlussgrenzen vor der ersten Änderung. Entfernen,
Hinzufügen, Umhängen sowie die Legacy-Felder der Pflanzen werden gemeinsam
committet.

`ConfigurationService.save_balcony()` normalisiert Balkon, Zeitzone, Tanks,
Pumpe, Ausgänge, Wände, Kalibrierungen, Automatik und die komplette
`planner_config`, bevor es schreibt. Alle beteiligten Repositories verwenden
danach dieselbe SQLite-Verbindung. Ein Fehler an beliebiger Stelle rollt die
gesamte Einstellungsseite zurück.

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
