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

Optional kannst du eine `.env`-Datei anlegen:

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

## Logs und Status

Logs anzeigen:

```bash
docker compose logs -f
```

Containerstatus anzeigen:

```bash
docker compose ps
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
| `PUID` | `1000` | User-ID für den Containerprozess |
| `PGID` | `1000` | Group-ID für den Containerprozess |
| `TZ` | `Europe/Berlin` | Zeitzone des Containers |
