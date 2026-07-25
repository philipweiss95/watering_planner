# Backend-Architektur

`server.py` ist nur noch der kompatible Einstiegspunkt. Bestehende Python-Aufrufer
können weiterhin `import server` verwenden; intern werden die Aufgaben im Paket
`watering_backend` getrennt:

- `core.py`: Anwendungsorchestrierung und kompatible API-Fassade
- `http_api.py`: Lebenszyklus des HTTP-Servers
- `database.py` und `schema.py`: SQLite-Verbindungen, Schema und Migrationen
- `validation.py`: zentrale Eingabe-, Enum- und Wertebereichsvalidierung
- `weather.py`: Normalisierung von Tages-, Stunden- und Simulationswetter
- `plant_model.py`: Pflanzenbedarf und einmalige Topf-Effizienz
- `connections.py`: globale, kapazitätsbegrenzte Anschlussoptimierung
- `scheduling.py` und `config.py`: Zeitfenster, zentrale Defaults und Validierung
- `forecast.py`: chronologische Haupt-/Vorratstank-Simulation
- `home_assistant.py`: Webhook-Transport
- `notifications.py`: SMTP, Deduplizierung, Entwarnung, Protokoll und Worker

Die Fachmodule importieren `core.py` nicht. Abhängigkeiten verlaufen von der
Orchestrierung zu den Fachmodulen, wodurch keine zirkulären Abhängigkeiten
entstehen.

## Datenbankmigrationen

Beim Start führt `init_db()` additive, wiederholbar ausführbare Migrationen aus.
Das aktuelle Schema hat `PRAGMA user_version = 3`. Jede Verbindung aktiviert
zusätzlich `PRAGMA foreign_keys = ON`.

- `watering_events.run_id` und `refill_events.run_id`
- partielle Unique-Indizes für beide nichtleeren `run_id`-Spalten
- `notification_state` für aktive/deduplizierte Zustände
- `notification_log` als persistentes Versand- und Fehlerprotokoll
- `notification_state.last_attempt_at`, `last_error` und
  `consecutive_failures` für kurze, persistente Wiederholungen nach SMTP-Fehlern
- `refill_window_observations` speichert Bedarf und Ausführbarkeit je
  Nachfüllfenster, damit verpasste Läufe erst nach Fensterende erkannt werden

Bestehende Zeilen bleiben unverändert; ihre `run_id` ist `NULL`. Tankbuchungen
mit neuer `run_id` laufen unter `BEGIN IMMEDIATE`, prüfen den Haupttank gegen den
kalibrierten Verbrauch und geben bei einem Retry das vorhandene Ergebnis zurück.

## Wettermodell

Open-Meteo liefert drei getrennte Bereiche:

- `planning_day` und die kompatiblen Top-Level-Werte stammen vollständig vom
  ersten Tag der Tagesprognose.
- `current` und `hourly` dienen ausschließlich Anzeige und Diagnose.
- Manuelle Werte tragen `mode: "simulation"` und `simulation: true`.

Der Zeitpunkt jedes erfolgreichen Abrufs wird persistent als
`last_successful_weather_fetch_at` gespeichert. Die Warnschwelle steht in
`planner_config.weather_stale_after_minutes`. Der davon unabhängige
`weather_cache_minutes`-Wert verhindert minütliche Open-Meteo-Aufrufe. Ein
manueller Abruf mit `GET /api/weather?force=true` umgeht den Cache. Bei einem
temporären Fehler können letzte gültige Daten mit `cache_fallback: true`
weiterverwendet werden. Zertifikatsfehler führen niemals zu einem unbestätigten
TLS-Abruf.

## Tankprognose

`depletion.forecast_events` enthält den sicheren, bis zu 16-tägigen
Open-Meteo-Horizont und einen als `estimated_weather` markierten
Extrapolationsbereich bis Tag 45. Jedes Ereignis enthält Zeitpunkt, Typ,
geplante Menge, beide Tankstände vor/nach dem Ereignis und `successful`,
`estimated` oder `unserved`. Vorratswasser wird nur mit dem konfigurierten
Pumpendurchsatz übertragen. Bei einem Gießlauf während des Transfers steht
daher nur die bis zu diesem Zeitpunkt geförderte Teilmenge im Haupttank bereit.

Neue eindeutige Felder:

- `last_supported_watering_at`
- `first_unserved_watering_at`
- `forecast_events`
- `main_tank_after_forecast_ml`
- `refill_tank_after_forecast_ml`

Die bisherigen Felder bleiben für vorhandene Browser- und HA-Aufrufer erhalten.
Sie stammen alle aus derselben Ereignissimulation:

- `last_watering_at` ist ein Alias für `last_supported_watering_at`, also den
  letzten lückenlos versorgten Lauf vor dem ersten Ausfall.
- `first_unserved_at` ist ein Alias für `first_unserved_watering_at`.
- `main_empty_at` bezeichnet den ersten Zeitpunkt, an dem der Haupttank einen
  vollständigen Lauf nicht mehr versorgen kann.
- `all_empty_at` bezeichnet diesen Zeitpunkt, wenn auch kein Vorratswasser für
  einen späteren Transfer vorhanden ist.
- Die Tankstände `*_after_forecast_ml` sind die simulierten Endstände; Haupt-
  und Vorratstank werden dabei nie einfach addiert.

## API

- `POST /api/settings`: validierte persistente Planer-Konfiguration
- `GET /api/weather?force=true`: manueller, erzwungener Wetterabruf
- `GET /api/diagnostics/notifications`: SMTP-Status ohne Passwort, aktive
  Warnungen und Protokoll
- `POST /api/diagnostics/notifications/check`: sofortiger Diagnosezyklus
- `POST /api/notifications/test`: Test-E-Mail senden
- `GET /api/diagnostics/home-assistant`: Konfiguration, letzter erfolgreicher
  Kontakt und letzte Fehlermeldung ohne Webhook-Geheimnis
- `POST /api/diagnostics/home-assistant/test`: prüft nur die Home-Assistant-
  Basis-API; der private Webhook wird dabei nicht aufgerufen

`POST /api/homekit/mark-run`, `POST /api/refill/mark-run`,
`POST /api/manual-run` und `POST /api/manual-refill` unterstützen `run_id`.
Home Assistant soll die vom manuellen Webhook empfangene `run_id` an den
Buchungsrequest weiterreichen. Die Beispiele tun dies.

Alte Aufrufer ohne `run_id` bleiben vorübergehend funktionsfähig. Der Server
erzeugt dann `legacy-<typ>-<uuid>` und kennzeichnet das Ergebnis mit
`legacy_generated_run_id: true`. Da ein alter Retry keine stabile Kennung
mitsendet, kann diese Übergangslösung nur Kompatibilität, nicht
Retry-Idempotenz garantieren.

## Persistente Konfiguration

`planner_config` umfasst:

- täglicher Bewässerungsbeginn und -ende
- maximale gemeinsame Tageszyklen und Mindestabstand
- beliebig viele überschneidungsfreie Nachfüllzeitfenster
- Mindestabstand zwischen Nachfüllungen
- Nachfüllstrategie `fraction` oder `target`
- Wetteralter, Versorgungstage, Warnungsruhezeit, Entwarnung und Workerintervall
- Wettercachezeit und kurze SMTP-Retryzeit nach fehlgeschlagenen Versuchen

Tankgrößen und Pumpendurchsatz bleiben in `balcony_settings` gespeichert.
Zeitformate, Überschneidungen, Kapazitäten, Koordinaten und nicht erfüllbare
Zyklusabstände werden serverseitig abgelehnt.

## SMTP-Umgebung

- `NOTIFICATIONS_ENABLED`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_FROM`
- `SMTP_TO` (mehrere Empfänger mit Komma oder Semikolon)
- `SMTP_SECURITY` mit `ssl`, `starttls` oder `none`
- `NOTIFICATION_WORKER_DISABLED` für Tests

`SMTP_PASSWORD` und Home-Assistant-Webhook-URLs werden weder in `/api/state`
noch in Diagnoseantworten ausgegeben.

## Tests und Risiken

Die Unit-Tests prüfen Tages-/Aktuellwetter, Simulationen, Sommer-/Winterzeit,
mehrere Nachfüllfenster, chronologische Tankversorgung, kalibrierten Verbrauch,
sequentielle und parallele `run_id`-Retries, globale Anschlusswahl,
Zusatzschläuche, alle Topfarten, flexible Zeitfenster, SMTP-Erfolg/Fehler,
Deduplizierung/Entwarnung und Migrationen. `.github/workflows/tests.yml` führt
Compile-, Python-, DOM-nahe JavaScript-Tests, Compose-Validierung und den
Container-Build bei Pushes und Pull Requests aus.

Verbleibende Risiken:

- Wetter- und SMTP-Verfügbarkeit hängen vom lokalen Netzwerk und DNS ab.
- Prognosen nach dem 16-Tage-Wetterhorizont schreiben den letzten sicheren
  Tageswert fort. Diese Ereignisse sind ausdrücklich als geschätzt markiert.
- Alte Aufrufer ohne stabile `run_id` können bei einem echten HTTP-Retry nicht
  dedupliziert werden und sollten auf die dokumentierten HA-Beispiele umsteigen.
