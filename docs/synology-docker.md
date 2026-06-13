# Docker-Betrieb auf Synology NAS

Diese Anleitung beschreibt den Betrieb des Terrassen-Bewässerungsplaners als Docker-Container auf einer Synology NAS mit Container Manager oder Docker Compose.

## Voraussetzungen

- Synology DSM mit installiertem **Container Manager**
- SSH-Zugriff auf die NAS, wenn du per Compose arbeitest
- Zugriff der NAS auf das Internet, damit Wetterdaten von Open-Meteo geladen werden können

Die App nutzt nur Python-Standardbibliothek und SQLite. Es gibt keine externen Python-Abhängigkeiten.

## Dateien

Für Docker sind diese Dateien relevant:

- `Dockerfile`: baut das Python-Image
- `docker-compose.yml`: gemeinsame Projektdatei für Docker Compose und Synology Container Manager
- `.env.synology.example`: Vorlage für private Synology-Einstellungen
- `.dockerignore`: hält lokale Datenbankdateien aus dem Image heraus
- `data/watering.sqlite3`: persistente SQLite-Datenbank, wird beim ersten Start automatisch angelegt

## Verzeichnis auf der NAS vorbereiten

Lege auf der NAS einen Ordner an, zum Beispiel:

```text
/volume1/docker/watering-planner
```

Kopiere den Projektinhalt in diesen Ordner. Wichtig ist, dass der Ordner `data` existiert und beschreibbar ist. Lege zusätzlich die private Synology-Umgebung an:

```bash
mkdir -p /volume1/docker/watering-planner/data
cd /volume1/docker/watering-planner
cp .env.synology.example .env.synology
```

Bearbeite `.env.synology` auf der NAS und trage dort die private Home-Assistant-Webhook-URL ein. Die Datei wird von Git ignoriert.

Wichtig: Der Dialog **Container Manager > Projekt > Erstellen** lädt keine vollständige Anwendung hoch. Er zeigt und importiert nur die Compose-Konfiguration. Da `docker-compose.yml` mit `build: .` arbeitet, müssen die Quelldateien vorher über **File Station**, Synology Drive, `scp` oder einen anderen Dateitransfer im ausgewählten NAS-Projektordner liegen.

Prüfe den Build-Kontext vor dem Erstellen des Projekts per SSH:

```bash
cd /volume1/docker/watering-planner
for path in Dockerfile docker-compose.yml .env.synology server.py public/index.html public/app.js public/styles.css data; do
  test -e "$path" && echo "OK: $path" || echo "FEHLT: $path"
done
docker compose -f docker-compose.yml config
```

Alle Pfade müssen mit `OK` erscheinen. Der letzte Befehl muss ohne Fehler durchlaufen. Wenn du ausschließlich die Synology-Oberfläche verwendest, kontrolliere denselben Ordner vorher in **File Station**. Versteckte Dateien wie `.env.synology` werden dort abhängig von den File-Station-Einstellungen nicht angezeigt; sie müssen trotzdem existieren.

Wenn du den Container nicht als root laufen lässt, setze die Rechte passend zu deinem Synology-Benutzer. Die numerischen IDs findest du per SSH mit:

```bash
id
```

Beispiel:

```bash
chown -R 1026:100 /volume1/docker/watering-planner/data
```

Die Werte `1026:100` sind nur ein Beispiel. Verwende die Ausgabe deiner NAS.

## Start mit Docker Compose

Wechsle per SSH in das Projektverzeichnis:

```bash
cd /volume1/docker/watering-planner
```

Danach bauen und starten:

```bash
docker compose -f docker-compose.yml up -d --build
```

Die App ist danach erreichbar unter:

```text
http://NAS-IP:8080
```

Wenn Port `8080` schon belegt ist, ändere die linke Portnummer in `docker-compose.yml`, zum Beispiel auf `"8090:8080"`.

## Start mit Synology Container Manager

1. Öffne **Container Manager**.
2. Öffne **Projekt > Erstellen**. Verwende nicht den Bereich **Container > Erstellen**.
3. Wähle als Projektpfad den bereits vollständig befüllten Ordner, zum Beispiel `/volume1/docker/watering-planner`.
4. Verwende als Quelle die Datei `docker-compose.yml`.
5. Kontrolliere in der YAML-Vorschau, dass `image: watering-planner:20260602-ui13` enthalten ist.
6. Starte das Projekt.

Die übrigen Dateien müssen in diesem Assistenten nicht einzeln angezeigt werden. Entscheidend ist, dass sie vorher im Projektpfad liegen: Docker verwendet diesen Ordner als Build-Kontext für `build: .`.

Container Manager verwaltet für ein Projekt eine eigene `docker-compose.yml`. Wenn du später Dateien im Projektordner aktualisierst, öffne im bestehenden Projekt **Details > Konfiguration YAML**, übernimm dort den aktuellen Inhalt aus `docker-compose.yml` und stelle die neuen Einstellungen bereit. Das reine Neuerstellen eines Containers verwendet sonst weiterhin die zuvor gespeicherte Projekt-YAML.

Wenn das Projekt bisher mit einer anderen Compose-Datei oder über SSH erstellt wurde, migriere es einmalig:

1. Projekt `watering-planner` in Container Manager stoppen und löschen. Den Projektordner und `data/` behalten.
2. Prüfen, dass im Projektordner `docker-compose.yml` und die private `.env.synology` liegen.
3. Unter **Projekt > Erstellen** denselben Projektordner wählen und `docker-compose.yml` als Quelle verwenden.
4. Projekt erstellen und starten.

Danach verwendet auch **Projekt > Aktion > Erstellen** die gespeicherte Synology-Projektdatei. SSH bleibt nur noch für Diagnosefälle nötig.

Die Synology-Dateien verwenden absichtlich:

```yaml
user: "0:0"
```

Das ist für Container Manager am unkompliziertesten, weil gemountete NAS-Ordner dann sofort beschreibbar sind. Wenn du den Container restriktiver laufen lassen möchtest, ersetze `0:0` durch die numerische Benutzer- und Gruppen-ID deiner NAS, zum Beispiel `1026:100`, und setze die Rechte des `data`-Ordners passend.

Wichtig: Beim Volume darf **Read-only** nicht aktiviert sein. Der Container muss in `/app/data` schreiben können, weil SQLite dort Datenbankdatei, Journal und temporäre Schreibdateien anlegt.

Wenn im Log diese Meldung steht:

```text
sqlite3.OperationalError: attempt to write a readonly database
```

dann ist fast immer eines davon die Ursache:

- der `data`-Ordner wurde im Container Manager schreibgeschützt gemountet
- `data/watering.sqlite3` wurde von einem anderen Benutzer kopiert und ist für den Container nicht beschreibbar
- der Container wurde noch mit einer alten Image-Version gestartet, in der er als nicht privilegierter `appuser` lief

Schneller Fix per SSH auf der NAS:

```bash
cd /volume1/docker/watering-planner
chmod -R u+rwX,go+rwX data
```

Danach Projekt im Container Manager neu bauen und starten.

Wenn Port `8080` schon belegt ist, ändere in `docker-compose.yml` die linke Portnummer:

```yaml
ports:
  - "8090:8080"
```

Die App wäre dann unter `http://NAS-IP:8090` erreichbar.

## Persistenz und Backup

Die Datenbank liegt im Container unter:

```text
/app/data/watering.sqlite3
```

Durch das Volume in `docker-compose.yml` wird sie auf der NAS gespeichert unter:

```text
./data/watering.sqlite3
```

Für Backups reicht normalerweise der Ordner:

```text
/volume1/docker/watering-planner/data
```

Stoppe den Container vor einem manuellen SQLite-Dateibackup, damit sicher keine Schreiboperation läuft:

```bash
docker compose -f docker-compose.yml stop
cp data/watering.sqlite3 data/watering.sqlite3.backup
docker compose -f docker-compose.yml up -d
```

## Updates

Nach Codeänderungen oder nach dem Kopieren einer neuen Version:

```bash
cd /volume1/docker/watering-planner
docker compose -f docker-compose.yml up -d --build
```

Die Datenbank bleibt erhalten, solange der Ordner `data` nicht gelöscht wird.

Im Synology **Container Manager** reicht ein normaler Neustart nicht aus, wenn sich Dateien im Image oder Einstellungen geändert haben. Ein Neustart verwendet weiter das bereits gebaute Image und die gespeicherte Projekt-YAML. Nach Änderungen an `public/index.html`, `public/app.js`, `public/styles.css`, `server.py`, `Dockerfile` oder `docker-compose.yml` immer:

1. Projekt öffnen und unter **Details > Konfiguration YAML** den aktuellen Inhalt aus `docker-compose.yml` übernehmen.
2. Neue Einstellungen bereitstellen.
3. Projekt **erstellen / bauen** und starten.
4. Falls Container Manager weiter die alte Oberfläche zeigt: das alte `watering-planner`-Image löschen und das Projekt danach erneut erstellen.
5. Im Browser hart neu laden.

Du erkennst die aktuelle Oberfläche daran, dass die ausgelieferte HTML-Datei diese Zeile enthält:

```html
<link rel="stylesheet" href="/styles.css?v=20260602-1">
```

Wenn im Browser oder per `curl http://NAS-IP:8080/` noch eine ältere `styles.css` ohne Versionsparameter oder ohne `app-nav` auftaucht, läuft auf der NAS noch ein altes Image oder ein Container aus einem anderen Projektordner.

Die Synology-Projektdatei verwendet deshalb ein versioniertes Image:

```yaml
image: watering-planner:20260602-ui13
```

Wenn nach dem Kopieren der neuen Dateien weiter ein 5964-Byte-HTML ohne `app-nav` ausgeliefert wird, wurde die neue Compose-Datei noch nicht verwendet. In dem Fall im Container Manager:

1. Projekt `watering-planner` stoppen und löschen. Den Projektordner und `data/` behalten.
2. Das alte `watering-planner`-Image löschen.
3. Projekt aus dem Ordner mit `docker-compose.yml` neu erstellen.
4. Nach dem Start prüfen:

```bash
curl http://NAS-IP:8080/ | grep app-nav
curl http://NAS-IP:8080/ | grep 'styles.css?v=20260602-1'
```

## Verwaisten Container-Manager-Eintrag reparieren

Wenn Container Manager beim Stoppen `No such container` meldet, zeigt die Oberfläche einen veralteten Eintrag an. Das kann nach dem Neuaufbau eines Projekts passieren: Der alte Container existiert in Docker nicht mehr, wird aber in der Synology-Oberfläche noch angezeigt.

Nicht den einzelnen Container-Eintrag weiterverwenden. Öffne in Container Manager den Bereich **Projekt** und stoppe oder lösche dort das Projekt `watering-planner`. Den Projektordner und insbesondere `data/` nicht löschen.

Falls auch die Projektansicht nicht mehr sauber funktioniert, per SSH neu erstellen:

```bash
cd /volume1/docker/watering-planner
sudo docker ps -a --format 'table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Image}}'
sudo docker ps --filter publish=8080 --format 'table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Ports}}'
sudo docker compose -f docker-compose.yml down --remove-orphans
sudo docker compose -f docker-compose.yml up -d --build
```

Falls der zweite `docker ps`-Befehl noch einen unerwarteten Container an Port `8080` zeigt, stoppe ihn über seine echte ID mit `sudo docker stop CONTAINER-ID` und führe anschließend die beiden `docker compose`-Befehle erneut aus.

Danach Container Manager neu laden. Falls der verwaiste Eintrag weiterhin sichtbar bleibt, Container Manager über das DSM Paket-Zentrum neu starten und das Projekt aus `docker-compose.yml` erneut öffnen. Das Volume `./data:/app/data` sorgt dafür, dass die SQLite-Datenbank bei diesem Neuaufbau erhalten bleibt.

## Logs und Status

Logs anzeigen:

```bash
docker compose -f docker-compose.yml logs -f
```

Containerstatus anzeigen:

```bash
docker compose -f docker-compose.yml ps
```

Healthcheck testen:

```bash
docker inspect --format='{{json .State.Health}}' watering-planner
```

## HomeKit und Kurzbefehle

Nutze in iOS Kurzbefehle die NAS-Adresse als Basis-URL, zum Beispiel:

```text
http://NAS-IP:8080
```

Die maschinenlesbare Blaupause liefert:

```text
http://NAS-IP:8080/api/shortcuts?base_url=http://NAS-IP:8080
```

Wenn die App außerhalb deines Heimnetzes erreichbar sein soll, verwende HTTPS über einen Reverse Proxy der Synology oder über dein bestehendes Netzwerksetup. Für reine HomeKit-Automationen im Heimnetz reicht in der Regel die lokale HTTP-Adresse.

## Relevante Environment-Variablen

| Variable | Standard | Beschreibung |
| --- | --- | --- |
| `HOST` | `0.0.0.0` | Interface im Container |
| `PORT` | `8080` | Port im Container |
| `DATA_DIR` | `/app/data` | Verzeichnis für SQLite-Daten |
| `TZ` | `Europe/Berlin` | Zeitzone des Containers |
| `HOME_ASSISTANT_WEBHOOK_URL` | Eintrag in `.env.synology` | Lokaler HA-Webhook für manuelle Sofortläufe |
| `HOME_ASSISTANT_REFILL_WEBHOOK_URL` | Eintrag in `.env.synology` | Lokaler HA-Webhook für manuelle Nachfüllläufe |
