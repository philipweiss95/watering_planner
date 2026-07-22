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

Auf dem iPhone lässt sich die Seite in Safari über **Teilen > Zum Home-Bildschirm** als **Gießplaner** installieren. Manifest, App-Icon, Standalone-Darstellung und Safe-Area-Abstände sind integriert. Für Service Worker und eine zuverlässige Web-App-Installation sollte die NAS-Adresse über HTTPS bereitgestellt werden.

Die SQLite-Datenbank wird beim ersten Start unter `data/watering.sqlite3` angelegt. Eine neue leere Datenbank startet mit Beispieldaten. Der anschließend im Webinterface gepflegte Datenbankbestand ist maßgeblich; Excel-Dateien im Datenordner werden nicht importiert.

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

Die Datenbank bleibt durch das Volume `./data:/app/data` erhalten. Die vollständige Synology-Anleitung steht unter [docs/synology-docker.md](docs/synology-docker.md). Für Docker Compose und den Synology Container Manager gibt es genau eine Projektdatei: `docker-compose.yml`. Private Werte werden auf der NAS in `.env.synology` hinterlegt.

Wenn die App außerhalb des Heimnetzes erreichbar ist, veröffentliche sie nur über HTTPS und mit Zugriffsschutz deines Reverse Proxys oder Netzwerks. Der Planner selbst hat keinen eingebauten Passwortschutz mehr.

Für die lokale Pumpensteuerung mit Meross Matter empfiehlt sich Home Assistant OS als VM und der Planer als separater Container auf der NAS. Die Anleitung steht unter [docs/home-assistant-vm.md](docs/home-assistant-vm.md). Direkt nutzbare Vorlagen liegen unter [home-assistant/configuration.yaml](home-assistant/configuration.yaml) und [home-assistant/automations.yaml](home-assistant/automations.yaml).

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

Der Planer verteilt die empfohlenen Zyklen gleichmäßig zwischen `07:00` und `19:00`. Bei vier Zyklen entstehen zum Beispiel die Zeitpunkte `07:00`, `11:00`, `15:00`, `19:00`. Manuell verbuchte Läufe zählen dabei mit. Home Assistant fragt den Planer alle 15 Minuten erneut ab. Dadurch geht ein Lauf nicht verloren, wenn eine einzelne Sensoraktualisierung ausfällt; ein verpasster geplanter Lauf wird später nachgeholt. Nach einem verbuchten Pumpenlauf setzt der Planer zusätzlich eine Sicherheitspause von 30 Minuten, damit Zyklen nicht direkt hintereinander starten.

Zusätzlich verwaltet der Planer einen 30-l-Vorratstank mit eigener Nachfüllpumpe. Die deaktivierbare Automatik arbeitet in zwei festen Nachtfenstern von `01:00` bis `02:00` und `06:00` bis `07:00`. Jedes Fenster kann höchstens einen Lauf auslösen; zwischen zwei Nachfüllvorgängen liegen immer mindestens drei Stunden. Jeder Lauf füllt die Hälfte des dann im Haupttank fehlenden Wassers nach und wird auf den Rest im Vorratstank begrenzt. Verpasste Fenster werden tagsüber nicht nachgeholt.

Beide Pumpen können unter **Einstellungen > Pumpen kalibrieren** anhand eines abgelesenen Füllstands von 0 bis 100 Prozent kalibriert werden. Für die Wasserpumpe wird aus dem letzten Vollstand beziehungsweise der letzten Messung, den Bewässerungszyklen, zwischenzeitlichen Nachfüllungen und dem prozentual gemessenen Haupttankstand ein Verbrauchsfaktor ermittelt. Er verändert ausschließlich die Tankbilanz, nie die ml-Angaben der Pflanzen. Für die Nachfüllpumpe berechnet der Planer aus dem prozentualen Vorratstankverlust und der protokollierten Pumpzeit einen neuen Durchsatz in ml/min und überschreibt damit den bisherigen Wert.

Version 1.0 enthält außerdem auf der **Info-Seite** einen internen Synology-Updater. Er liest ausschließlich stabile GitHub-Releases, prüft die SHA-256-Prüfsumme, sichert die bestehende Programmversion, baut beide Container neu und führt bei einem Fehler einen Rollback aus. Der GitHub-Token wird dauerhaft nur im persistenten `data`-Volume gespeichert und nicht an den Browser zurückgegeben. Zu jedem Release wird der passende Abschnitt aus [CHANGELOG.md](CHANGELOG.md) veröffentlicht und vor sowie nach der Installation im Updater angezeigt.

Der Updater erneuert sich am Ende eines Updates über einen unabhängigen, kurzlebigen Übergabecontainer. Dadurch bleibt der Compose-Befehl aktiv, während der alte Updater ersetzt wird. Seit Version 1.3.1 bestätigt der Helfer zusätzlich das erwartete Image und den Health-Status. Erst wenn anschließend genau ein Updater-Container übrig ist, wird das Update als erfolgreich markiert. Ein gestarteter Updater kann unterbrochene Ersetzungen außerdem selbst bereinigen und den kanonischen Containernamen wiederherstellen.

Die Berechnung nutzt Temperatur, Tagesniederschlag, Wind, FAO-Referenzverdunstung ET₀, Sonnenscheindauer, Balkon-/Terrassenausrichtung in Grad, Koordinaten, Pflanzenpositionen und die vier Wandhöhen nach Seite. Wenn Open-Meteo keine ET₀-Werte liefert oder manuelle Wetterdaten genutzt werden, schätzt die App ET₀ aus Temperatur, Sonnenscheindauer und Wind. Zusätzlich wird Wind als Balkon-Expositionsfaktor berechnet: hohe Windgeschwindigkeiten erhöhen Transpiration und Topfverdunstung, niedrige Wände schützen weniger.

Für die Tank-Reichweite nutzt die App die längste reguläre Open-Meteo-Vorhersage von 16 Tagen. Erst wenn beide Tanks laut Prognose länger reichen, wird der mittlere Tagesverbrauch dieser Vorhersage für die weitere Schätzung verwendet.

Der Wasserbedarf pro Pflanze wird aus einem Pflanzenprofil berechnet:

```text
Tagesbedarf ≈ ET₀ × Pflanzenkoeffizient × wirksame Kronenfläche
            + Substratverdunstung der Topffläche
            - wirksam aufgefangener Niederschlag
```

Der rohe ET0-Kübelwert wird anschließend mit einem Terrassen-Tropfbewässerungsfaktor kalibriert und zusätzlich saisonal gewichtet. In der Weboberfläche wird er als ein einzelner Versorgungsfaktor angezeigt: `100%` ist die aktuelle Standardversorgung, `120%` erhöht den berechneten Tagesbedarf um 20%. Eine Live-Vorschau zeigt die konkrete Auswirkung auf den täglichen Pflanzenbedarf. Ende Mai liegt der Pflanzenbedarf durch die saisonale Gewichtung unter dem Hochsommerwert; im Juli/August steigt er je nach Pflanzengruppe wieder an. Das bildet die beobachtete Praxis ab, dass die reale Anlage mit wenigen gemeinsamen Pumpzyklen auskommt und der unkalibrierte ET0-Ansatz deutlich zu hohe Tagesmengen liefert. Die Kronenfläche wird aus Pflanzenart, Pflanzengröße und Topfvolumen abgeleitet. Topfarten mit Depot, Überlauf oder geschlossenem Topf verändern die nutzbare Wassermenge und das Überwässerungsrisiko.

Der Pflanzenkatalog enthält typische Balkonpflanzen aus Gemüse, Kräutern, Beerenobst, Blühpflanzen, mediterranen Gehölzen, Kletterpflanzen und Sukkulenten.

## Verschlauchung und Beschattung

Alle Pflanzen werden bei jedem Pumpenzyklus gleichzeitig gegossen. Unter `Schläuche` wird jeder physische Schlauch einmal mit seinem 15-, 30- oder 60-ml-Output angelegt. Unter `Pflanzen` werden anschließend ein oder mehrere Schläuche ausgewählt. Daraus berechnet der Planer automatisch die tatsächlich gelieferte Wassermenge pro Pflanze und pro Pumpenzyklus.

Zusätzlich berechnet das Tool einen festen Anschlussvorschlag für einen mittelwarmen Auslegungstag mit 4 gemeinsamen Zyklen. Er dient als Hinweis, wenn eine Pflanze dauerhaft besser an einer anderen Schlauchkombination hängen sollte. Das Wetter verändert nur die Anzahl gemeinsamer Pumpzyklen, nicht die eingetragene Verschlauchung. Jeder Ausgang hat maximal 12 Anschlüsse; diese Grenze wird beim Vorschlag für die 15-, 30- und 60-ml-Ausgänge einzeln geprüft.

Die Pflanzen können in der Draufsicht des Balkons platziert werden. Aus Position, Ausrichtung, Sonnenstand und Wandhöhen wird für jede Pflanze ein eigener Beschattungsfaktor berechnet.

## HomeKit / iOS Kurzbefehle

Die Weboberfläche zeigt die Kurzbefehle-Schritte und URLs an. Maschinenlesbar gibt es sie hier:

```text
GET /api/shortcuts?base_url=https://deine-domain.example
```

Ein manueller Sofortlauf kann über die Weboberfläche oder per iPhone-Kurzbefehl angefordert werden:

```text
POST /api/manual-run
Content-Type: application/json

{"auto_weather":true}
```

Der Planer prüft zuerst, ob Tank und Verschlauchung einen vollständigen Zyklus zulassen. Anschließend ruft er den konfigurierten lokalen Home-Assistant-Webhook auf. Home Assistant schaltet die Steckdose und verbucht den Lauf nach dem Ausschalten.
Der Endpunkt ist für das lokale Heimnetz gedacht. Wenn der Planer über einen Reverse Proxy aus dem Internet erreichbar ist, muss `/api/manual-run` dort zusätzlich geschützt werden.

Nach einem real ausgeführten Pumpenlauf kann HomeKit den Lauf verbuchen:

```text
POST /api/homekit/mark-run
Content-Type: application/json

{"auto_weather":true,"slot":"morning"}
```

Dadurch zählt der Server die bereits erledigten Tageszyklen und reduziert den Haupttankstand.
Die letzten Läufe erscheinen in der Übersicht als Protokoll unter `Bewässerungsvorgänge`.

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
- `GET /api/watering-events`: Protokoll der letzten Bewässerungs-, Nachfüll- und Tankfüllstand-Ereignisse
- `POST /api/manual-run`: vollständigen Pumpzyklus sofort über Home Assistant anfordern
- `POST /api/manual-refill`: Nachfülllauf sofort über Home Assistant anfordern
- `POST /api/homekit/mark-run`: Pumpenlauf verbuchen und Tank reduzieren
- `POST /api/refill/mark-run`: nächtlichen Nachfülllauf verbuchen und Wasser vom Vorratstank in den Haupttank rechnen
- `POST /api/tanks/main/fill`: Haupttank als voll markieren
- `POST /api/tanks/refill/fill`: 30-l-Vorratstank als voll markieren
- `POST /api/calibration/main`: Hauptpumpenfaktor aus `measured_level_percent` kalibrieren
- `POST /api/calibration/refill`: Nachfüllpumpen-Durchsatz aus `measured_level_percent` kalibrieren
- `GET /api/update/status`: internen Updater-Status lesen
- `POST /api/update/setup`, `/api/update/check`, `/api/update/install`: stabilen GitHub-Updater verwalten
- `POST /api/automation/pause`: Automatik bis morgen pausieren
- `POST /api/automation/resume`: Automatik wieder aktivieren
