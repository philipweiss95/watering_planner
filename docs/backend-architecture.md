# Backend-Architektur

Der Backendcode folgt einer gerichteten Schichtenstruktur:

```text
server.py
  -> watering_backend/core.py          Kompatibilitätsfassade
  -> watering_backend/app.py           Composition Root
       -> api/*                         HTTP-Adapter
       -> services/*                    Fachlogik
       -> repositories/*                SQL und Transaktionen
       -> database.py / schema.py       Verbindung und Migrationen
```

`services` und `api` importieren weder `core` noch `server`. Repositories
enthalten den fachlich gruppierten SQLite-Zugriff. Die Services erhalten
Repositories, Uhren, Netzwerktransporte und andere Service-Ports explizit.

## Module

### Anwendung und Konfiguration

- `app.py`: erzeugt eine `Application`, verdrahtet alle Komponenten und
  koordiniert Initialisierung, Worker, Serverstart und Shutdown.
- `core.py`: schmale, patchbare Kompatibilitätsfassade für bestehende
  `import server`-Aufrufer.
- `config.py`: Planerkonfiguration, Zeitvalidierung und lokale Tagesgrenzen.
- `catalog.py`: Pflanzenkatalog, Wasserprofile und fachliche Konstanten.
- `models.py`: `TypedDict`- und `Literal`-Typen für Wetter, Planung,
  Tankereignisse, Diagnose und Benachrichtigungen.
- `validation.py`: zentrale Validierung von Pflanzen, Ausgängen, Wänden,
  Tanks, Koordinaten, Enums und endlichen Zahlen.

### Datenhaltung

- `database.py`: instanzgebundene `Database`; jede Verbindung setzt
  `row_factory`, `PRAGMA foreign_keys = ON` und `busy_timeout`.
- `schema.py`: Basisschema, additive idempotente Migrationen und Seeds.
- `repositories/settings.py`: persistente Planer- und Kalibrierwerte.
- `repositories/plants.py`: Pflanzenkatalog und Pflanzen-CRUD.
- `repositories/hoses.py`: Schläuche, Zuordnung und Legacy-Spiegelwerte.
- `repositories/tanks.py`: Balkon, Tanks, Ausgänge und Wände.
- `repositories/events.py`: Bewässerung, Nachfüllung, Tankfüllung,
  Kalibrierung und Nachfüllfenster-Beobachtungen.
- `repositories/refill_runs.py`: persistente Reservierungen und
  Zustandsübergänge physischer Nachfüllläufe.
- `repositories/notifications.py`: Warnungszustände und Versandprotokoll.

SQL steht ausschließlich in `schema.py`, `database.py` und den Repositories.
HTTP-Routen enthalten keine SQL-Strings.

### Fachservices

- `services/evaluation.py`: Pflanzenbedarf, Sonne, Schatten, ET0,
  Saisonmodell und Zusammenstellung des Tagesergebnisses.
- `services/routing.py`: globale Anschlussoptimierung und Bewertung der
  festen Verschlauchung.
- `services/scheduling.py`: lokale Zeit, DST-sichere Tagesfenster,
  Bewässerungszeitpunkte, Pausen und Cooldowns.
- `services/weather.py`: Open-Meteo-Abruf, persistenter Cache,
  Tages-/Aktuell-/Stundenwerte und manuelle Simulation.
- `services/forecast.py`: Verbrauchstage und chronologische Tankprognose.
- `services/refill.py`: Nachfüllfenster, Status, Cooldown,
  verpasste Fenster und manuelle Nachfüllpläne.
- `services/refill_runs.py`: atomarer Start, Laufbestätigung, Abschluss,
  Fehler, Ablauf und Diagnose persistenter Nachfüllläufe.
- `services/watering.py`: atomare, idempotente Bewässerungsbuchungen sowie
  manuelle Tankfüllungen; der alte Nachfüllaufruf bleibt nur als
  Kompatibilitätsschicht.
- `services/calibration.py`: Kalibrierung von Haupt- und Nachfüllpumpe.
- `services/home_assistant.py`: Diagnose und Webhook-Transport.
- `services/notifications.py`: Ermittlung der Warnungsbedingungen.
- `services/diagnostics.py`: Wetter- und öffentlicher SMTP-Status.
- `services/state.py`: stabiles `/api/state`-Dokument.
- `services/history.py`: browserfreundliche Ereignisdarstellung.
- `services/updater.py`: interner Updater-Client.

### HTTP

- `api/router.py`: Registrierung nach HTTP-Methode und Pfad, Pfadparameter,
  verzögertes JSON-Lesen und gemeinsame Clientfehler-Abbildung.
- `api/handler.py`: `SimpleHTTPRequestHandler`-Adapter, Sicherheitsheader
  und statischer GET-Fallback.
- `api/routes_*.py`: kleine Routen für Zustand, Pflanzen, Schläuche, Tanks,
  Automatik, Diagnose und Updates.
- `api/responses.py`: einheitliche JSON-Ein-/Ausgabe.

Alle bisherigen URLs und Antwortformate bleiben erhalten.

## Wetterauswertung

1. Die Route liest manuelle Werte oder ruft `WeatherService.fetch_weather()`.
2. Der Service prüft den persistenten Cache und sperrt parallele Abrufe mit
   einem prozesssicheren Lock.
3. Open-Meteo-Tageswerte bilden `planning_day`; `current` und `hourly`
   bleiben reine Anzeige- und Diagnosewerte.
4. `EvaluationService` berechnet Pflanzenbedarf und Zyklen ausschließlich
   aus den konsistenten Tageswerten.
5. `ForecastService` baut daraus chronologische Ereignisse.
6. Der erfolgreiche Abrufzeitpunkt und ein separater letzter Fehler werden
   in den Einstellungen gespeichert.

`force=true` umgeht den Cache. Ein TLS-Zertifikatsfehler löst keinen
unbestätigten zweiten Request aus.

## Bewässerungslauf

1. API oder Home Assistant übergibt `run_id` und Laufdaten.
2. `WateringService.mark_run()` öffnet `Database.connection(immediate=True)`.
3. `EventsRepository` sucht die `run_id` innerhalb derselben Transaktion.
4. Bei einem Treffer wird das vorhandene Ergebnis unverändert zurückgegeben.
5. Andernfalls wird der Haupttank gegen `consumed_per_cycle_ml` geprüft.
6. Ereignis und Tankabzug werden gemeinsam gespeichert.
7. Jeder Fehler rollt beide Änderungen zurück.

Die partiellen Unique-Indizes auf nichtleeren `run_id`-Werten sind die zweite
Schutzlinie gegen parallele Doppelbuchungen.

## Nachfüllung

1. `RefillService` bewertet aktuelle Tankstände, Zeitfenster, Bedarf,
   Pumpendurchsatz, Cooldown und bisherige Ereignisse.
2. Beim Start und bei jeder Statusprüfung werden die absoluten Fenster für
   heute und morgen in `refill_window_plans` vorgemerkt. Der Schlüssel enthält
   Datum sowie Start und Ende in UTC und bleibt dadurch über DST-Wechsel
   eindeutig.
3. Bedarf, technische Ausführbarkeit, erwartete Transfermenge und die
   Beobachtung innerhalb des Fensters werden persistent und idempotent
   fortgeschrieben. Tankbuchungen aktualisieren offene Pläne unmittelbar;
   ein atomarer Zeitstempelvergleich verhindert, dass eine ältere parallele
   Berechnung einen neueren Zustand überschreibt. Ein verbuchtes
   Nachfüllereignis erfüllt den Plan.
4. Nach Fensterende wird ein ausführbarer, benötigter und nicht erfüllter Plan
   als `window_missed` gemeldet. Historische Pläne bleiben unverändert;
   Konfigurationsänderungen stornieren nur noch nicht begonnene Zukunftspläne.
5. `RefillRunService.start()` reserviert vor dem Einschalten unter
   `BEGIN IMMEDIATE` die feste Menge, Dauer, Tank-Ausgangswerte und das exakte
   Fenster. Bei automatischen Läufen begrenzt die verbleibende Fensterzeit
   abzüglich zehn Sekunden Sicherheitsreserve die Freigabe.
6. Home Assistant meldet nach dem Ausschalten dieselbe `run_id`. Das Fenster
   darf zu diesem Zeitpunkt bereits geschlossen sein.
7. Der Abschluss verwendet keine neu geplante Menge. Physischer
   Vorratsverbrauch, im Haupttank bilanzierbarer Zugang, Ereignis,
   Reservierungsstatus und Fenstererfüllung werden atomar gespeichert.
8. Doppelte oder parallele Abschlussmeldungen geben dasselbe persistierte
   Ergebnis zurück. Ablauf und unklare Pumpenzustände bleiben als manuelle
   Prüfhinweise sichtbar.
9. Die Prognose stellt Wasser erst entsprechend Pumpendurchsatz und
   Fertigstellungszeit im Haupttank bereit.

Vorratswasser versorgt niemals direkt einen Bewässerungslauf.

## Transaktionsgrenzen

- `Database.connection()` führt Commit oder vollständigen Rollback aus.
- `immediate=True` setzt `BEGIN IMMEDIATE` vor idempotenten Tankbuchungen.
- `watering_events.run_id` und `refill_events.run_id` besitzen partielle
  Unique-Indizes.
- `refill_runs.run_id` ist Primärschlüssel; ein partieller Unique-Index auf
  `active_slot` erlaubt höchstens einen reservierten oder laufenden
  Nachfüllvorgang.
- Nachfüllabschluss, Tankbilanz, Ereignis und Fenstererfüllung teilen eine
  `BEGIN IMMEDIATE`-Transaktion.
- Schlauch-Snapshot und vollständige Einstellungsseite verwenden jeweils eine
  gemeinsame Transaktion über alle beteiligten Repositories.
- Die Benachrichtigungsentscheidung, der Logeintrag und der neue
  Benachrichtigungszustand laufen in einer gemeinsamen
  `NotificationsRepository`-Transaktion.
- Kalibrierungsereignis und gemessener Tankstand werden gemeinsam gespeichert.

## Migrationen

`Application.initialize()` führt `schema.initialize()` bei jedem Start aus.
Die Schritte sind additiv und wiederholbar; `PRAGMA user_version` ist aktuell
`6`.

Wichtige Ergänzungen gegenüber 1.4.2:

- `run_id` und Unique-Indizes für Bewässerung und Nachfüllung
- `actual_consumed_ml`
- flexible Tank-, Positions- und Wetterfelder
- `notification_state` und `notification_log`
- SMTP-Versuch, letzter Erfolg, Fehler und Fehlerzähler
- `refill_window_observations`
- `refill_window_plans` mit absoluten UTC-Grenzen, Bedarf,
  Ausführbarkeit, Transfermenge, Erfüllung und Stornierung
- `refill_runs` mit Zwei-Phasen-Status, reservierter Menge und Dauer,
  Start-/Ablaufzeiten, physischer und bilanzierter Menge sowie
  Konsistenzhinweisen und persistenten Abgleichsmetadaten

Legacy-Pflanzen und Schlauchzuordnungen werden ohne Duplikate übernommen.
Ein realitätsnahes 1.4.2-Fixture wird zweimal migriert und auf Datenerhalt,
Fremdschlüssel und Schema-Version geprüft.

## Hintergrundworker

`Application.start_notification_worker()` startet nur, wenn SMTP aktiviert
und `NOTIFICATION_WORKER_DISABLED` nicht gesetzt ist. Der Worker:

1. prüft regelmäßig die injizierten Benachrichtigungsbedingungen,
2. verwendet den Wettercache statt minütlicher Netzabrufe,
3. persistiert Deduplizierung und Retryzustand,
4. überlebt einzelne Prüf- oder SMTP-Fehler,
5. wird beim Server-Shutdown mit `stop()` und `join()` beendet.

Das Intervall ist serverseitig auf 10 bis 3600 Sekunden begrenzt. Die
Nachfüllfenstererkennung hängt dennoch nicht von einem Worker-Lauf im offenen
Fenster ab, weil `Application.initialize()` und jede Statusprüfung heute und
morgen persistent vorplanen.

Gespeicherte Altwerte von 1 bis 86400 Sekunden werden beim Lesen einmalig auf
10 bis 3600 Sekunden normalisiert. Dadurch starten bestehende Installationen
weiterhin; neue Konfigurationen müssen die engere Grenze bereits bei der API-
Validierung einhalten.

## Kompatibilität

`server.py` bleibt der ausführbare Einstiegspunkt. Während der
Übergangsphase aliasiert es `watering_backend.core`, damit ältere Tests und
Integrationen weiterhin `server.DATA_DIR`, `server.local_now` oder
`server.urlopen` patchen können. Dieser Mechanismus ist auf die kleine
Kompatibilitätsfassade begrenzt; neue Tests importieren `Application`,
Repositories, Services oder den Router direkt.

Alte Aufrufer ohne `run_id` erhalten weiterhin
`legacy-<typ>-<uuid>`. Das erhält die Aufrufkompatibilität, kann aber einen
erneuten alten Request ohne stabile Kennung nicht deduplizieren.

## Neue Route

1. Die benötigte Operation dem `ApiContext`-Protocol hinzufügen.
2. Sie in `Application` als Delegation an Repository oder Service anbieten.
3. Eine kleine Funktion im passenden `api/routes_*.py` schreiben.
4. Die Funktion in `register(router)` mit Methode und unverändertem Pfad
   registrieren.
5. Routertest mit Fake-Kontext und Integrationstest mit realer
   `Application` ergänzen.

Routen sollen nur HTTP übersetzen. Validierung gehört nach `validation.py`,
SQL in ein Repository und Fachentscheidungen in einen Service.

## Geheimnisse

SMTP-Konfiguration kommt ausschließlich aus:

- `NOTIFICATIONS_ENABLED`
- `SMTP_HOST`, `SMTP_PORT`
- `SMTP_USERNAME`, `SMTP_PASSWORD`
- `SMTP_FROM`, `SMTP_TO`
- `SMTP_SECURITY`
- `NOTIFICATION_WORKER_DISABLED` für Tests

Passwörter und Home-Assistant-Webhook-URLs erscheinen weder in
`/api/state` noch in Diagnoseantworten.

## Verbleibende technische Schulden

- `server.py` nutzt für alte patchende Aufrufer noch den dokumentierten
  Modulalias; neue Aufrufer sollen direkt `Application` verwenden.
- Die zwei großen Legacy-Testmodule bleiben als Kompatibilitäts-Suite
  bestehen. Neue Tests sind bereits nach Services, Router, Migration,
  Regression, Architektur und Transaktionen getrennt.
- Prognoseereignisse nach dem sicheren Open-Meteo-Horizont schreiben den
  letzten Tageswert fort und sind deshalb ausdrücklich als geschätzt markiert.
