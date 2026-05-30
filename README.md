# Terrassen-Bewässerungsplaner

Mobil-first Web-App zur Planung und serverseitigen Entscheidung von Terrassen-Bewässerungszyklen.

## Start lokal

```bash
python3 server.py
```

Danach im Browser öffnen:

```text
http://127.0.0.1:8080
```

Die SQLite-Datenbank wird beim ersten Start unter `data/watering.sqlite3` angelegt. Liegt `data/table.xlsx` daneben, wird dieser Bestand als Grundlage importiert: Pflanzenname, Schlauchnummern, Größe und ml pro Pumpzyklus werden übernommen, die Positionen werden zunächst verteilt und können danach in der Draufsicht verschoben werden. Ohne Excel-Datei startet die App mit Beispieldaten.

## Docker / Synology NAS

Die App kann als Docker-Container laufen. Das Image nutzt Python ohne externe Abhängigkeiten, bindet im Container auf `0.0.0.0:8080` und speichert die SQLite-Datenbank persistent unter `/app/data`.

Lokal oder auf der NAS:

```bash
docker compose up -d --build
```

Danach öffnen:

```text
http://NAS-IP:8080
```

Die Datenbank bleibt durch das Volume `./data:/app/data` erhalten. Die vollständige Synology-Anleitung steht unter [docs/synology-docker.md](docs/synology-docker.md). Für den Synology Container Manager gibt es zusätzlich eine explizite Datei ohne Variablen: `compose.synology.yaml`.

Für die lokale Pumpensteuerung mit Meross Matter empfiehlt sich Home Assistant OS als VM und der Planer als separater Container auf der NAS. Die Anleitung steht unter [docs/home-assistant-vm.md](docs/home-assistant-vm.md).

## Uberspace

Die App nutzt nur die Python-Standardbibliothek und SQLite. Auf Uberspace sollte sie daher mit Python 3.11+ laufen. Wichtig: Uberspace nutzt ohne explizite Version weiterhin Python 2.7 als Default, deshalb immer `python3.13`, `python3.14` oder eine andere Python-3-Version verwenden.

Beispiel-Service unter `~/etc/services.d/watering-planner.ini`:

```ini
[program:watering-planner]
directory=/home/USER/watering_planner
command=/usr/bin/env HOST=0.0.0.0 PORT=8080 python3.13 server.py
startsecs=10
```

Danach:

```bash
supervisorctl reread
supervisorctl update
uberspace web backend set / --http --port 8080
```

## Wetter und Sonnenstand

Der Standort wird als Koordinaten gespeichert. Das frühere Freitextfeld ist für die Berechnung nicht nötig und wird in der Oberfläche nicht mehr abgefragt. In der Weboberfläche kann der Browser die Koordinaten per Ortung eintragen. Die Zeitzone wird ebenfalls automatisch gesetzt und dient Open-Meteo zur korrekten Tagesprognose. Die Wetterdaten werden serverseitig über Open-Meteo abgerufen:

```text
GET /api/weather
```

Für HomeKit reicht dann:

```text
GET /api/homekit/check?auto=true
```

Die Antwort enthält zwei Entscheidungen:

- `should_run`: Heute besteht grundsätzlich noch Bewässerungsbedarf.
- `run_now`: Ein lokaler Automations-Controller wie Home Assistant soll im aktuellen Zeitfenster jetzt einen Zyklus starten.

Der Planer kennt dafür die Tagesfenster `07:00`, `10:00`, `11:00`, `14:00`, `15:00`, `16:00`, `17:00` sowie ein Notfallfenster um `19:00`. Wenn die verbleibenden Zyklen im Tagesverlauf knapp werden, setzt der Planer `run_now` früher auf `true`, damit die Zyklen nicht erst spät am Tag nachgeholt werden müssen.

Die Berechnung nutzt Temperatur, Tagesniederschlag, Wind, FAO-Referenzverdunstung ET₀, Sonnenscheindauer, Balkon-/Terrassenausrichtung in Grad, Koordinaten, Pflanzenpositionen und die vier Wandhöhen nach Seite. Wenn Open-Meteo keine ET₀-Werte liefert oder manuelle Wetterdaten genutzt werden, schätzt die App ET₀ aus Temperatur, Sonnenscheindauer und Wind. Zusätzlich wird Wind als Balkon-Expositionsfaktor berechnet: hohe Windgeschwindigkeiten erhöhen Transpiration und Topfverdunstung, niedrige Wände schützen weniger.

Der Wasserbedarf pro Pflanze wird aus einem Pflanzenprofil berechnet:

```text
Tagesbedarf ≈ ET₀ × Pflanzenkoeffizient × wirksame Kronenfläche
            + Substratverdunstung der Topffläche
            - wirksam aufgefangener Niederschlag
```

Der rohe ET0-Kübelwert wird anschließend mit einem Terrassen-Tropfbewässerungsfaktor von 3% kalibriert und zusätzlich saisonal gewichtet. Ende Mai liegt der Pflanzenbedarf dadurch unter dem Hochsommerwert; im Juli/August steigt er je nach Pflanzengruppe wieder an. Das bildet die beobachtete Praxis ab, dass die reale Anlage mit wenigen gemeinsamen Pumpzyklen auskommt und der unkalibrierte ET0-Ansatz deutlich zu hohe Tagesmengen liefert. Die Kronenfläche wird aus Pflanzenart, Pflanzengröße und Topfvolumen abgeleitet. Topfarten mit Depot, Überlauf oder geschlossenem Topf verändern die nutzbare Wassermenge und das Überwässerungsrisiko.

Der Pflanzenkatalog enthält typische Balkonpflanzen aus Gemüse, Kräutern, Beerenobst, Blühpflanzen, mediterranen Gehölzen, Kletterpflanzen und Sukkulenten.

## Verschlauchung und Beschattung

Alle Pflanzen werden bei jedem Pumpenzyklus gleichzeitig gegossen. Deshalb berechnet das Tool zuerst einen festen Anschlussplan, der unabhängig vom aktuellen Wetter ist und nur einmal umgesetzt werden soll. Das Wetter verändert danach nur noch die Anzahl gemeinsamer Pumpzyklen, nicht die Verschlauchung. Der feste Plan ist auf einen mittelwarmen Auslegungstag mit 4 gemeinsamen Zyklen kalibriert. Eine Pflanze kann mehrere Schläuche bekommen, zum Beispiel `1x 30 ml + 1x 15 ml` statt `1x 60 ml`, wenn der feste Bedarf pro Zyklus näher bei 45 ml liegt. Der Vorschlag nutzt nie mehr Schläuche als im aktuellen Bestand der Pflanze hinterlegt sind; weniger Schläuche sind möglich. Jeder Ausgang hat maximal 12 Anschlüsse; diese Grenze wird beim Optimieren für die 15-, 30- und 60-ml-Ausgänge einzeln geprüft. Die Oberfläche weist darauf hin, wenn eine Pflanze dauerhaft besser an einem anderen Anschlusstyp oder mit einer anderen Schlauchkombination hängen sollte.

Die Pflanzen können in der Draufsicht des Balkons platziert werden. Aus Position, Ausrichtung, Sonnenstand und Wandhöhen wird für jede Pflanze ein eigener Beschattungsfaktor berechnet.

## HomeKit / iOS Kurzbefehle

Die Weboberfläche zeigt die Kurzbefehle-Schritte und URLs an. Maschinenlesbar gibt es sie hier:

```text
GET /api/shortcuts?base_url=https://deine-domain.example
```

Nach einem real ausgeführten Pumpenlauf kann HomeKit den Lauf verbuchen:

```text
POST /api/homekit/mark-run
Content-Type: application/json

{"auto_weather":true,"slot":"morning"}
```

Dadurch zählt der Server die bereits erledigten Tageszyklen und reduziert den Tankstand.
Die letzten Läufe erscheinen im Dashboard als Protokoll unter `Bewässerungsvorgänge`.

## API

- `GET /api/state`: kompletter Zustand für die Weboberfläche
- `GET /api/weather`: aktuelle Wetter- und Tagesprognose für die gespeicherten Koordinaten
- `GET /api/shortcuts`: iOS-Kurzbefehle-Blaupause
- `POST /api/balcony`: Balkon/Terrasse, Grad-Ausrichtung, Koordinaten, Wände, Tank und Pumpenausgänge speichern
- `POST /api/plants`: Pflanze hinzufügen
- `POST /api/plants/{id}/position`: Position einer Pflanze in der Balkon-Draufsicht speichern
- `DELETE /api/plants/{id}`: Pflanze entfernen
- `POST /api/evaluate`: Empfehlung berechnen, mit `{"auto_weather": true}` automatisch
- `GET /api/homekit/check`: kompakte HomeKit-Entscheidung
- `GET /api/watering-events`: Protokoll der letzten Bewässerungsläufe
- `POST /api/homekit/mark-run`: Pumpenlauf verbuchen und Tank reduzieren
- `POST /api/automation/pause`: Automatik bis morgen pausieren
- `POST /api/automation/resume`: Automatik wieder aktivieren
