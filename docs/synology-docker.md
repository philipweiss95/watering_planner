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
- `compose.yaml`: startet den Container mit persistenter Datenablage
- `compose.synology.yaml`: Synology-Variante ohne Variablen, direkt für Container Manager
- `.dockerignore`: hält lokale Datenbankdateien aus dem Image heraus
- `data/watering.sqlite3`: persistente SQLite-Datenbank, wird beim ersten Start automatisch angelegt

## Verzeichnis auf der NAS vorbereiten

Lege auf der NAS einen Ordner an, zum Beispiel:

```text
/volume1/docker/watering-planner
```

Kopiere den Projektinhalt in diesen Ordner. Wichtig ist, dass der Ordner `data` existiert und beschreibbar ist:

```bash
mkdir -p /volume1/docker/watering-planner/data
```

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

Optional kannst du eine `.env`-Datei anlegen, wenn du den Container nicht als root laufen lassen möchtest:

```env
APP_PORT=8080
PUID=1026
PGID=100
```

Danach bauen und starten:

```bash
docker compose up -d --build
```

Die App ist danach erreichbar unter:

```text
http://NAS-IP:8080
```

Wenn Port `8080` schon belegt ist, setze in `.env` zum Beispiel:

```env
APP_PORT=8090
```

Dann erreichst du die App unter:

```text
http://NAS-IP:8090
```

## Start mit Synology Container Manager

1. Öffne **Container Manager**.
2. Erstelle ein neues Projekt.
3. Wähle als Pfad den Ordner mit diesem Projekt, zum Beispiel `/volume1/docker/watering-planner`.
4. Verwende `compose.synology.yaml`, wenn du ohne `.env` und ohne variable Platzhalter arbeiten möchtest.
5. Starte das Projekt.

Die Synology-Datei verwendet absichtlich:

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

Wenn Port `8080` schon belegt ist, ändere in `compose.synology.yaml` die linke Portnummer:

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

Durch das Volume in `compose.yaml` wird sie auf der NAS gespeichert unter:

```text
./data/watering.sqlite3
```

Für Backups reicht normalerweise der Ordner:

```text
/volume1/docker/watering-planner/data
```

Stoppe den Container vor einem manuellen SQLite-Dateibackup, damit sicher keine Schreiboperation läuft:

```bash
docker compose stop
cp data/watering.sqlite3 data/watering.sqlite3.backup
docker compose up -d
```

## Updates

Nach Codeänderungen oder nach dem Kopieren einer neuen Version:

```bash
cd /volume1/docker/watering-planner
docker compose up -d --build
```

Die Datenbank bleibt erhalten, solange der Ordner `data` nicht gelöscht wird.

Im Synology **Container Manager** reicht ein normaler Neustart nicht aus, wenn sich Dateien im Image geändert haben. Ein Neustart verwendet weiter das bereits gebaute Image. Nach Änderungen an `public/index.html`, `public/app.js`, `public/styles.css`, `server.py` oder `Dockerfile` immer:

1. Projekt stoppen.
2. Projekt **neu erstellen / neu bauen**.
3. Falls Container Manager weiter die alte Oberfläche zeigt: das alte `watering-planner`-Image löschen und das Projekt danach erneut erstellen.
4. Im Browser hart neu laden.

Du erkennst die aktuelle Oberfläche daran, dass die ausgelieferte HTML-Datei diese Zeile enthält:

```html
<link rel="stylesheet" href="/styles.css?v=20260531-8">
```

Wenn im Browser oder per `curl http://NAS-IP:8080/` noch eine ältere `styles.css` ohne Versionsparameter oder ohne `app-nav` auftaucht, läuft auf der NAS noch ein altes Image oder ein Container aus einem anderen Projektordner.

Die Synology-Compose-Datei verwendet deshalb ein versioniertes Image:

```yaml
image: watering-planner:20260531-ui10
```

Wenn nach dem Kopieren der neuen Dateien weiter ein 5964-Byte-HTML ohne `app-nav` ausgeliefert wird, wurde die neue Compose-Datei noch nicht verwendet. In dem Fall im Container Manager:

1. Projekt `watering-planner` stoppen und löschen. Den Projektordner und `data/` behalten.
2. Das alte `watering-planner`-Image löschen.
3. Projekt aus dem Ordner mit `compose.synology.yaml` neu erstellen.
4. Nach dem Start prüfen:

```bash
curl http://NAS-IP:8080/ | grep app-nav
curl http://NAS-IP:8080/ | grep 'styles.css?v=20260531-8'
```

## Verwaisten Container-Manager-Eintrag reparieren

Wenn Container Manager beim Stoppen `No such container` meldet, zeigt die Oberfläche einen veralteten Eintrag an. Das kann nach dem Neuaufbau eines Projekts passieren: Der alte Container existiert in Docker nicht mehr, wird aber in der Synology-Oberfläche noch angezeigt.

Nicht den einzelnen Container-Eintrag weiterverwenden. Öffne in Container Manager den Bereich **Projekt** und stoppe oder lösche dort das Projekt `watering-planner`. Den Projektordner und insbesondere `data/` nicht löschen.

Falls auch die Projektansicht nicht mehr sauber funktioniert, per SSH neu erstellen:

```bash
cd /volume1/docker/watering-planner
sudo docker ps -a --format 'table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Image}}'
sudo docker ps --filter publish=8080 --format 'table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Ports}}'
sudo docker compose -f compose.synology.yaml down --remove-orphans
sudo docker compose -f compose.synology.yaml up -d --build
```

Falls der zweite `docker ps`-Befehl noch einen unerwarteten Container an Port `8080` zeigt, stoppe ihn über seine echte ID mit `sudo docker stop CONTAINER-ID` und führe anschließend die beiden `docker compose`-Befehle erneut aus.

Danach Container Manager neu laden. Falls der verwaiste Eintrag weiterhin sichtbar bleibt, Container Manager über das DSM Paket-Zentrum neu starten und das Projekt aus `compose.synology.yaml` erneut öffnen. Das Volume `./data:/app/data` sorgt dafür, dass die SQLite-Datenbank bei diesem Neuaufbau erhalten bleibt.

## Logs und Status

Logs anzeigen:

```bash
docker compose -f compose.synology.yaml logs -f
```

Containerstatus anzeigen:

```bash
docker compose -f compose.synology.yaml ps
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
| `APP_PORT` | `8080` | Host-Port in `compose.yaml` |
| `PUID` | `0` | User-ID für den Containerprozess in `compose.yaml` |
| `PGID` | `0` | Group-ID für den Containerprozess in `compose.yaml` |
| `TZ` | `Europe/Berlin` | Zeitzone des Containers |
