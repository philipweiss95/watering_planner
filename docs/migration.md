# Migration auf das modulare Backend und die neue Oberfläche

Die SQL-Migration selbst läuft beim ersten Start automatisch und wiederholbar.
Für Installationen auf Version 1.4.2 erfolgt die Übernahme bewusst in zwei
Updater-Schritten.

## Updatepfad ab 1.4.2

1. Zuerst ausschließlich das stabile Release **1.4.3** veröffentlichen und über
   **Info > Updates** installieren.
2. Prüfen, dass Planner und Updater beide wieder erreichbar sind und die
   Oberfläche Version 1.4.3 meldet.
3. Erst danach **1.5.0** als stabiles Release veröffentlichen. Solange 1.4.3
   nicht installiert ist, darf 1.5.0 nicht das neueste stabile Release sein.
4. Erneut über **Info > Updates** prüfen und 1.5.0 installieren.
5. Während beider Updates das Browserfenster geöffnet lassen, bis der jeweilige
   Updater den erfolgreichen Containerwechsel bestätigt.

Version 1.4.3 ändert die Bewässerungsfachlogik nicht. Sie erweitert den
bisherigen Updater lediglich so, dass er das neue Verzeichnis
`watering_backend/` bei 1.5.0 übernehmen, sichern und bei einem Fehler
zurückrollen kann.

## Vorher

1. Den aktuellen Planner-Container stoppen, damit SQLite konsistent gesichert
   wird.
2. `data/watering.sqlite3`, `.env.synology`, die Compose-Datei und deine aktiven
   Home-Assistant-Dateien sichern.
3. Den bisherigen Image-Tag oder Projektstand für einen Rollback notieren.
4. Prüfen, dass das Backup lesbar ist und nicht nur eine leere Datei enthält.

Beispiel auf der NAS:

```bash
cd /volume1/docker/watering-planner
docker compose stop watering-planner
cp data/watering.sqlite3 "data/watering.sqlite3.backup-$(date +%Y%m%d-%H%M%S)"
cp .env.synology ".env.synology.backup-$(date +%Y%m%d-%H%M%S)"
```

## Programm und Datenbank

1. Der Updater übernimmt den vollständigen Projektstand einschließlich
   `watering_backend/`, `public/js/`, `public/css/`, `public/sw.js` und
   `server.py`.
2. Das bestehende `data`-Volume bleibt unverändert eingebunden.
3. `NOTIFICATIONS_ENABLED` für den ersten Start von 1.5.0 auf `false` lassen.
4. Beim Start ergänzt `init_db()` automatisch bis Schema-Version 3:
   `run_id`-Spalten, Unique-Indizes sowie `notification_state` und
   `notification_log`. Schema 3 ergänzt additive SMTP-Versuchsfelder und
   `refill_window_observations`. Vorhandene Ereignisse und Tankstände bleiben
   erhalten; alle SQLite-Verbindungen erzwingen danach Fremdschlüssel.
5. Nach dem Start `GET /api/health` und `GET /api/state` prüfen. Optional per
   SQLite `PRAGMA user_version;` kontrollieren; erwartet wird `3`.

Alte Aufrufer ohne `run_id` funktionieren übergangsweise weiter. Sie sind bei
einem HTTP-Retry aber nicht idempotent. Deshalb müssen alle produktiven
Home-Assistant-Buchungen auf eine stabile `run_id` umgestellt werden.

## Home Assistant

1. Die Änderungen aus `home-assistant/configuration.yaml` und
   `home-assistant/automations.yaml` in die aktiven HA-Dateien übertragen.
2. Eigene Entity-IDs und die privaten Webhook-IDs beibehalten; niemals die
   Platzhalter aus dem Repository produktiv verwenden.
3. Sicherstellen, dass die vom manuellen Webhook empfangene `run_id` bis zu
   `/api/homekit/mark-run` beziehungsweise `/api/refill/mark-run` weitergegeben
   wird.
4. In Home Assistant YAML prüfen und die betroffenen Skripte/Automationen neu
   laden; bei Änderungen an `configuration.yaml` vollständig neu starten.
5. Im Planner unter **System** zuerst den sicheren Home-Assistant-Test
   ausführen. Dieser testet nur `/api/` und löst keinen Pumpen-Webhook aus.
6. Einen echten Lauf erst danach beaufsichtigt testen und im Verlauf prüfen,
   dass genau ein Ereignis mit `run_id` entstanden ist.

## Einstellungen

Nach der Migration einmal kontrollieren und speichern:

- Haupt- und Vorratstankgröße
- täglicher Bewässerungsbeginn und -ende
- maximale Tageszyklen und Mindestabstand
- alle Nachfüllfenster und deren Mindestabstand
- Nachfüllstrategie, Anteil oder Zielmenge
- Pumpendurchsatz und bestehende Kalibrierungsfaktoren
- Wetteralter, Wettercache, Reichweitenwarnung, Ruhezeit, kurze Fehler-Retryzeit
  und Entwarnung

Die frühere Annahme eines festen 30-Liter-Vorratstanks gilt nicht mehr. Ein
vorhandener Wert wird migriert und kann jetzt geändert werden.

## SMTP und Worker

SMTP wird ausschließlich serverseitig konfiguriert:

```text
NOTIFICATIONS_ENABLED
SMTP_HOST
SMTP_PORT
SMTP_USERNAME
SMTP_PASSWORD
SMTP_FROM
SMTP_TO
SMTP_SECURITY
```

1. Variablen in `.env.synology` ergänzen und den Container neu erstellen, damit
   die Umgebung übernommen wird.
2. Zunächst deaktiviert lassen und im Bereich **System** den Konfigurationsstatus
   prüfen.
3. `NOTIFICATIONS_ENABLED=true` setzen, Container erneut erstellen und eine
   Test-E-Mail senden.
4. Prüfen, dass Versand oder Fehler im Benachrichtigungsprotokoll erscheint.
5. `NOTIFICATION_WORKER_DISABLED` im Produktivbetrieb nicht auf `true` setzen.

## iPhone-PWA

Der Service Worker verwendet denselben Anwendungsversion-Cache und kennt alle
neuen Module. Nach dem Containerwechsel:

1. Die PWA vollständig schließen und erneut öffnen.
2. Einmal bei aktiver Netzwerkverbindung neu laden, damit der neue Worker die
   Kontrolle übernimmt.
3. Bleibt wider Erwarten die alte Oberfläche sichtbar, Website-Daten für die
   Planner-Adresse löschen oder das Home-Screen-Symbol entfernen und neu
   hinzufügen.
4. Navigation, Safe Areas, Prognose-Scroll und einen Dialog auf dem echten
   iPhone prüfen.

## Abnahme

- Hauptkarte nennt nächste Aktion und Zeitpunkt korrekt.
- Tageszeitleiste entspricht den HA-Zeitpunkten.
- Prognose zeigt 16 Tage und denselben ersten Ausfall wie die API.
- Pflanzen- und Schlauchzuordnungen sind vollständig.
- Ein doppelter Buchungsrequest mit gleicher `run_id` verändert keinen Tank
  zweimal.
- Wetter, Home Assistant, SMTP und Datenbank haben im Systembereich den
  erwarteten Status.
- Keine Browser-API zeigt SMTP-Passwort oder Webhook-URL.

## Rollback

1. Neue Container stoppen.
2. Vorherigen Programmstand beziehungsweise Image-Tag wiederherstellen.
3. Das gesicherte SQLite-Backup zurückkopieren. Die Migration ist additiv, aber
   für einen garantiert identischen Altzustand ist das Backup der sichere Weg.
4. Vorherige `.env.synology` und bei Bedarf HA-Dateien zurückspielen.
5. Vorherige Container starten und `/api/health` sowie Tankstände prüfen.

Erst nach erfolgreicher Abnahme sollten Benachrichtigungen und unbeaufsichtigte
Automationen wieder aktiviert werden.
