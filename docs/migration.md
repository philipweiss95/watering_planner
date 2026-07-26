# Migration auf das modulare Backend und die neue Oberfläche

Die SQL-Migration läuft beim ersten Start automatisch und wiederholbar. Die am
25. Juli 2026 veröffentlichte Version **1.4.3** ist die unveränderliche
Brückenversion für dieses Update und auf der Synology bereits installiert. Sie
muss weder neu erstellt noch erneut veröffentlicht werden.

Der Git-Tag `v1.4.3` zeigt über das Tag-Objekt `f08acb6c` auf Commit
`e02ceb198`. Der Branch `main` enthält weiterhin Version 1.4.2 und ist deshalb
nicht die technische Ausgangsbasis dieser Migration.

Das veröffentlichte Paket `watering-planner-1.4.3.zip` enthält 26 Dateien,
deren Inhalt mit diesem Commit übereinstimmt. Seine verifizierte SHA-256-Summe
lautet
`78a38685d19c0952541bf96f9286b8eb11c5df0c2edc8de3a9bd285ed9f5ce7a`.

## Updatepfad von 1.4.3 auf 1.5.0

1. Unter **Info > Updates** prüfen, dass die installierte Oberfläche
   **Version 1.4.3** anzeigt. Ist dort eine andere Version zu sehen, das Update
   nicht starten.
2. SQLite-Datenbank und `.env.synology` wie unten beschrieben sichern.
3. Version **1.5.0** mit dem bereits installierten Updater aus 1.4.3
   installieren.
4. Das Browserfenster geöffnet lassen, bis der Updater den erfolgreichen
   Containerwechsel bestätigt.
5. Nach dem Neustart Schema, Tankstände, Pflanzen, Katalog-IDs,
   Schlauchzuordnungen und Standortdaten prüfen.

Ein direkter Sprung von 1.4.2 auf 1.5.0 ist nicht unterstützt. Der in 1.4.2
enthaltene Updater verwaltet die modularen Zielpfade noch nicht. Auf einer
solchen Installation muss deshalb zuerst das bereits veröffentlichte,
unveränderte Release 1.4.3 installiert werden. Der ab 1.5 enthaltene Updater
prüft zusätzlich die installierte `VERSION` und meldet
`update_requires_v1_4_3_bridge`, bevor er ein weiteres Release herunterlädt.
Diese zusätzliche Prüfung ersetzt den notwendigen Zwischenschritt für einen
noch laufenden 1.4.2-Updater nicht.

Version 1.4.3 ändert die Bewässerungsfachlogik nicht. Ihr Updater verwaltet bei
Installation und Datei-Rollback genau diese Pfade:

```text
server.py
watering_backend/
public/
updater/
home-assistant/
docs/
scripts/
.github/
Dockerfile
docker-compose.yml
.dockerignore
.gitignore
.env.synology.example
README.md
CHANGELOG.md
VERSION
package.json
```

Damit wird `watering_backend/` aus dem 1.5.0-Paket vollständig übernommen. Beim
Rollback werden alle verwalteten Zielpfade zuerst entfernt und nur die zuvor
vorhandenen Dateien aus der Sicherung wiederhergestellt. Neu mit 1.5.0
hinzugekommene verwaltete Pfade bleiben daher nicht zurück. Das Verzeichnis
`data/`, die SQLite-Datenbank und `.env.synology` gehören ausdrücklich nicht zu
den verwalteten Pfaden und bleiben unangetastet.

Vor der Übernahme vergleicht der 1.4.3-Updater die SHA-256-Prüfsumme, verlangt
die erwartete ZIP-Wurzel, weist absolute Pfade und Pfadtraversierung zurück und
prüft Pflichtdateien sowie die Paketversion. Danach baut er Planner und Updater,
wartet auf den Planner-Healthcheck und übergibt den eigenen Austausch an einen
separaten Hilfscontainer. Der Abschluss wird erst gemeldet, wenn das erwartete
Updater-Image gesund läuft und genau ein Updater-Container übrig ist.

## Vorher

1. Den aktuellen Planner-Container stoppen, damit SQLite konsistent gesichert
   wird.
2. `data/watering.sqlite3`, `.env.synology` und deine aktiven
   Home-Assistant-Dateien sichern.
3. Den bisherigen Image-Tag oder Projektstand für einen Rollback notieren.
4. Prüfen, dass das Backup lesbar ist und nicht nur eine leere Datei enthält.
5. Den Planner wieder starten und erst danach das Update in der Oberfläche
   aufrufen.

Beispiel auf der NAS:

```bash
cd /volume1/docker/watering-planner
docker compose stop watering-planner
cp data/watering.sqlite3 "data/watering.sqlite3.backup-$(date +%Y%m%d-%H%M%S)"
cp .env.synology ".env.synology.backup-$(date +%Y%m%d-%H%M%S)"
docker compose start watering-planner
```

## Programm und Datenbank

1. Der Updater übernimmt den vollständigen Projektstand einschließlich
   `watering_backend/`, `public/js/`, `public/css/`, `public/sw.js` und
   `server.py`.
2. Das bestehende `data`-Volume und `.env.synology` bleiben unverändert.
3. `NOTIFICATIONS_ENABLED` für den ersten Start von 1.5.0 auf `false` lassen.
4. Beim Start ergänzt `init_db()` automatisch bis Schema-Version 4:
   `run_id`-Spalten, Unique-Indizes sowie `notification_state` und
   `notification_log`. Schema 3 ergänzt additive SMTP-Versuchsfelder und
   `refill_window_observations`. Schema 4 ergänzt die persistenten,
   zeitzonensicheren `refill_window_plans` und übernimmt vorhandene
   Beobachtungen idempotent. Vorhandene Pflanzen einschließlich `size=tree`,
   Ereignisse und Tankstände bleiben erhalten; alle SQLite-Verbindungen
   erzwingen danach Fremdschlüssel.
5. Nach dem Start `GET /api/health` und `GET /api/state` prüfen. Optional per
   SQLite `PRAGMA user_version;` kontrollieren; erwartet wird `4`.

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

Der automatische Datei-Rollback bei einem Installationsfehler stellt die
verwalteten Programmdateien aus seiner 1.4.3-Sicherung wieder her. Er entfernt
dabei auch verwaltete Pfade, die erst mit 1.5.0 hinzugekommen sind, und baut den
Planner erneut. Die Übergabe an den neuen Updater erfolgt erst nach dem
Planner-Healthcheck; bis dahin bleibt daher der laufende 1.4.3-Updater aktiv.
`data/` und `.env.synology` werden dabei nicht verändert.

Da die Datenbankmigration von 1.5.0 additiv ist, kann 1.4.3 grundsätzlich mit
den zusätzlich vorhandenen Tabellen und Spalten starten. Ein reiner
Datei-Rollback entfernt jedoch weder neue Datenbankstrukturen noch Buchungen,
die bereits unter 1.5.0 entstanden sind. Für einen garantiert identischen
1.4.3-Datenstand ist deshalb die Sicherung von vor dem Update erforderlich:

1. Planner und Updater stoppen.
2. Den Datei-Rollback auf den gesicherten 1.4.3-Programmstand ausführen.
3. Nur für den exakten alten Datenstand die aktuelle SQLite-Datei beiseitelegen
   und das vor dem Update erzeugte SQLite-Backup zurückkopieren.
4. `.env.synology` nur zurückspielen, falls sie nach der Sicherung manuell
   verändert wurde; der Updater selbst ändert sie nicht.
5. Planner und Updater starten. Danach `/api/health`, Version, Tankstände,
   Pflanzen und Schläuche prüfen.

Erst nach erfolgreicher Abnahme sollten Benachrichtigungen und unbeaufsichtigte
Automationen wieder aktiviert werden.

## Grenze der automatischen Updater-Prüfung

Die CI verwendet den unveränderten Updater-Code aus dem Tag `v1.4.3`, spielt
damit das 1.5.0-Paket ein und prüft Dateiübernahme sowie Rollback gegen
Fehlerfälle. Planner- und Updater-Images werden zusätzlich real gebaut; der
migrierte Planner wird im Container gestartet und über seine APIs geprüft.

Der Selbsttausch des bereits laufenden Updater-Containers benötigt jedoch den
Docker-Socket und die echten Bind-Mount-Pfade des Synology-Hosts. Dieser letzte
Handoff wird in CI mit kontrollierten Docker-Antworten simuliert und kann die
Host-spezifischen Rechte, Pfade und Container-Namen der Synology nicht
vollständig beweisen. Deshalb nach der Installation manuell prüfen:

1. Genau ein Container `watering-planner-updater` läuft.
2. Planner und Updater zeigen Version 1.5.0 beziehungsweise verwenden die
   Images mit Tag 1.5.0.
3. Beide Healthchecks sind grün.
4. `data/` und `.env.synology` sind weiterhin unverändert eingebunden.

Der Release-Workflow lädt außerdem die bereits veröffentlichten
1.4.3-Bridge-Assets erneut. Er prüft ihre GitHub-Metadaten, Paketgröße und die
fest hinterlegten SHA-256-Werte. Normale Unit-Tests bleiben dabei vollständig
offline. Eine zusätzliche Tag-Prüfung belegt, dass der 1.4.2-Updater die
modularen Pfade noch nicht verwaltet, der 1.4.3-Updater dagegen
`watering_backend/` und `package.json` übernimmt.

- `watering-planner-1.4.3.zip`:
  `78a38685d19c0952541bf96f9286b8eb11c5df0c2edc8de3a9bd285ed9f5ce7a`
- Prüfsummen-Asset:
  `32dc99123f053d5530292023bb5e951a83c44c068f745e138cb8728ec123f0bf`

Zusätzlich werden alle 26 Dateien im Bridge-Paket bytegenau mit Commit
`e02ceb198264104fd8f2bc68eb8db7b24ac00dc8` verglichen.
