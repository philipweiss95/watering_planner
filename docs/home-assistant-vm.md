# Home Assistant OS als VM und Planer als Container

Diese Variante trennt die Aufgaben sauber:

- Home Assistant OS läuft als VM auf der Synology und übernimmt Matter, Automationen, Add-ons und Backups.
- Der Bewässerungsplaner läuft als kleiner Docker-Container im Synology Container Manager.
- Home Assistant fragt den Planer lokal ab und schaltet die Meross Matter Steckdose nur dann, wenn ein Zyklus ansteht.

## Zielbild

```text
Meross Matter Steckdose  <->  Home Assistant OS VM
                                  |
                                  | REST im lokalen Netz
                                  v
                         watering-planner Container
```

Der Planer ist lokal erreichbar unter:

```text
http://NAS-IP:8080
```

Home Assistant ist lokal erreichbar unter:

```text
http://HA-IP:8123
```

## 1. Home Assistant OS in Synology VMM installieren

1. In DSM **Virtual Machine Manager** installieren und öffnen.
2. Unter **Image** das Home Assistant OS Image importieren.
   - Verwende das offizielle HAOS-Image für `Generic x86-64`.
   - Wenn VMM ein Festplattenformat verlangt, nutze das KVM/qcow2-Image oder importiere nach Synology-Anleitung.
3. Neue VM erstellen:
   - CPU: 2 Kerne
   - RAM: mindestens 2 GB, besser 4 GB
   - Netzwerk: Bridge auf dein LAN
   - Autostart aktivieren
4. VM starten.
5. Home Assistant öffnen:

```text
http://HA-IP:8123
```

6. Onboarding abschließen.
7. In Home Assistant unter **Einstellungen > System > Backups** automatische Backups aktivieren.

## 2. Bewässerungsplaner als Container starten

Lege auf der NAS einen Ordner an:

```text
/volume1/docker/watering-planner
```

Kopiere dieses Projekt dort hinein. Der Ordner sollte mindestens enthalten:

```text
Dockerfile
docker-compose.yml
.env.synology
server.py
public/
data/
```

Der Dialog **Container Manager > Projekt > Erstellen** importiert nur die Compose-Konfiguration. Die oben genannten Dateien werden dort nicht einzeln angezeigt und müssen vorab über **File Station** oder einen anderen Dateitransfer im Projektpfad liegen. Prüfe den Ordner vor dem Erstellen bei Bedarf per SSH:

```bash
cd /volume1/docker/watering-planner
for path in Dockerfile docker-compose.yml .env.synology server.py public/index.html public/app.js public/styles.css data; do
  test -e "$path" && echo "OK: $path" || echo "FEHLT: $path"
done
docker compose -f docker-compose.yml config
```

Der im Webinterface gepflegte SQLite-Datenbankbestand unter `data/watering.sqlite3` ist maßgeblich. Eine vorhandene `table.xlsx` wird nicht importiert.

Container Manager:

1. **Container Manager > Projekt > Erstellen**
2. Projektpfad: `/volume1/docker/watering-planner`
3. Auf der NAS `.env.synology.example` nach `.env.synology` kopieren und dort die lokale Home-Assistant-Webhook-URL ergänzen:

```yaml
HOME_ASSISTANT_WEBHOOK_URL=http://HA-IP:8123/api/webhook/WEBHOOK-ID
```

Verwende möglichst die feste LAN-IP von Home Assistant statt `homeassistant.local`. Der Name mit `.local` funktioniert im Browser häufig, wird aus einem Docker-Container aber nicht immer zuverlässig aufgelöst.
Verwende für `WEBHOOK-ID` eine zufällige, nicht erratbare Zeichenfolge und trage exakt denselben Wert in `home-assistant/automations.yaml` unter `webhook_id` ein. Eine neue ID erzeugst du zum Beispiel mit `uuidgen | tr '[:upper:]' '[:lower:]' | tr -d '-'`.
Alternativ öffnest du nach dem Import in Home Assistant die Automation **Bewaesserung - Manueller Sofortlauf**, bearbeitest den Webhook-Trigger und verwendest die dort angezeigte bzw. neu erzeugte Webhook-ID. Trage genau diese private ID anschließend in `.env.synology` auf der NAS ein. Eine ID aus einer README, einem öffentlichen Repository oder einem Chat nicht übernehmen.

4. Compose-Datei: `docker-compose.yml`
5. Projekt starten

Danach öffnen:

```text
http://NAS-IP:8080
```

## 3. Meross Matter Steckdose in Home Assistant bringen

Wenn die Steckdose bereits in Apple Home ist:

1. Apple Home öffnen.
2. Matter-Gerät teilen bzw. Kopplungscode für einen weiteren Controller erzeugen.
3. Home Assistant Companion App öffnen.
4. Matter-Gerät zu Home Assistant hinzufügen.
5. In Home Assistant prüfen, welche Entity-ID die Steckdose bekommt, zum Beispiel:

```text
switch.meross_pumpe
```

Diese Entity-ID brauchst du später in der Automation.

## 4. `configuration.yaml` vorbereiten

Die fertige Vorlage liegt unter [`home-assistant/configuration.yaml`](../home-assistant/configuration.yaml). Nach `/config/configuration.yaml` kopieren oder die enthaltenen Abschnitte in eine vorhandene Datei übernehmen. `NAS-IP` durch die IP der Synology ersetzen, zum Beispiel `192.168.178.116`.

Wichtig:

- `webhook:`, `sensor:`, `rest_command:`, `script:` und `automation:` stehen ganz links auf oberster YAML-Ebene.
- `webhook:` lädt den HTTP-Endpunkt `/api/webhook/...` für den manuellen Sofortlauf.
- Wenn `automation: !include automations.yaml` bereits existiert, nicht doppelt einfügen.
- Nach Änderungen an `configuration.yaml` Home Assistant neu starten.

Nach dem Neustart gibt es den Sensor:

```text
sensor.bewaesserung_planner
```

Der Sensor steht im aktiven Zeitfenster auf `True`, wenn ein Pumpenlauf ansteht. Zwischen den Zeitfenstern ist `False` normal, auch wenn für den Tag noch Zyklen offen sind.

## 5. `automations.yaml` anlegen

Die fertige Vorlage liegt unter [`home-assistant/automations.yaml`](../home-assistant/automations.yaml). Nach Home Assistant kopieren:

```text
/config/automations.yaml
```

Die Vorlagen trennen die Aufgaben:

- `script.bewaesserung_pumpzyklus` in `configuration.yaml` schaltet die Meross-Steckdose `switch.smart_plug_mini` für 120 Sekunden ein, schaltet sie wieder aus und verbucht den Lauf.
- `script.bewaesserung_nachfuellen` schaltet die zweite Meross-Steckdose `switch.smart_plug_mini_refill` fuer die vom Planner berechnete Dauer ein und verbucht danach den Nachfuelllauf.
- `Bewaesserung - Tagesfenster` prüft alle 15 Minuten den Sensor und startet das Skript nur bei `run_now`.
- `Bewaesserung - Manueller Sofortlauf` nimmt über einen Webhook mit zufälliger ID einen sofortigen manuellen Lauf entgegen und startet dasselbe Skript.
- `Bewaesserung - Haupttank nachfuellen` prüft in den Fenstern `01:00` bis `02:00` und `06:00` bis `07:00` alle 15 Minuten den Planner. Jedes Fenster kann die zweite Pumpe höchstens einmal starten; zwischen Nachfüllvorgängen liegen mindestens drei Stunden. Voraussetzung sind ein vorhandener Nachfüllbedarf, Wasser im 30-l-Vorratstank und eine aktive Nachfüllautomatik. Pro Lauf wird wegen der verbundenen Behälter nur die Hälfte des aktuell fehlenden Haupttank-Inhalts gepumpt. Verpasste Fenster werden tagsüber nicht nachgeholt.
- `Bewaesserung - Manuelle Nachfuellung` nimmt über einen zweiten Webhook einen vom Dashboard angeforderten Nachfülllauf entgegen und startet dasselbe Nachfüllskript.

Falls die Steckdosen in Home Assistant andere Entity-IDs erhalten haben, `switch.smart_plug_mini` und `switch.smart_plug_mini_refill` in der Vorlage `configuration.yaml` ersetzen.

Die Sensoraktualisierung muss vor der Bedingung stehen. Der zuvor gespeicherte Sensorzustand stammt sonst möglicherweise noch aus der Zeit zwischen zwei Fenstern und ist dort absichtlich `False`.

Der Planer verteilt die empfohlenen Pumpenläufe gleichmäßig zwischen `07:00` und `19:00`. Bei vier Zyklen sind die geplanten Zeitpunkte zum Beispiel `07:00`, `11:00`, `15:00`, `19:00`. Manuell verbuchte Läufe zählen dabei mit. Ein verpasster Zeitpunkt wird bei einer späteren Prüfung nachgeholt.

Der Planer verhindert Überbewässerung, weil nach jedem Lauf `remaining_cycles_today` sinkt. Zusätzlich bleibt `run_now` nach einem verbuchten Lauf 30 Minuten lang gesperrt. Die Skripte und Automationen laufen im Modus `single`, damit ein zweiter Trigger während eines laufenden Pumpenzyklus nicht direkt einen weiteren Lauf startet. Home Assistant muss dadurch keine eigene Verteilungslogik kennen.

Der Haupttank wird rechnerisch aus dem separaten 30-l-Vorratstank nachgefüllt. Die Laufzeit ergibt sich aus der Hälfte des aktuell fehlenden Haupttank-Inhalts und dem in den Planner-Einstellungen hinterlegten Durchsatz der Nachfüllpumpe in ml/min. Die Automatik ist auf jeweils einen Lauf in den Nachtfenstern `01:00` bis `02:00` und `06:00` bis `07:00` begrenzt. Eine feste Mindestpause von drei Stunden verhindert zu dicht aufeinanderfolgende Nachfüllungen. Außerhalb dieser Fenster gibt der Planner keinen automatischen Lauf frei; ein verpasstes Fenster wird nicht nachgeholt. Wenn der Vorratstank leer oder die Nachfüllautomatik im Webinterface deaktiviert ist, bleibt die Pumpe ebenfalls gesperrt. Der manuelle Nachfüllbutton benötigt zusätzlich `HOME_ASSISTANT_REFILL_WEBHOOK_URL` in der Server-Umgebung.

Die Anzeige, wann Haupttank und Vorratstank leer sind, nutzt bis zu 16 Tage Open-Meteo-Vorhersage. Nur wenn die rechnerische Reichweite darüber hinausgeht, extrapoliert der Planner mit dem Durchschnittsverbrauch dieser Prognosetage.

Im Dashboard des Planers wird zusätzlich angezeigt:

- ob Home Assistant jetzt starten darf
- nächstes Zeitfenster
- ob ein verpasster verteilter Lauf nachgeholt wird
- ob die Automatik bis morgen pausiert ist
- Protokoll der letzten Bewässerungsvorgänge mit Zeitpunkt, Menge, Temperatur und Regen

Über die Buttons **Heute pausieren** und **Pause aufheben** kann die Automatik direkt im Planer gesperrt oder wieder freigegeben werden.
Der Button **Zyklus jetzt starten** fordert unabhängig vom aktuellen Zeitfenster einen vollständigen Lauf an. Der Planer sperrt den Button, wenn Tank, Verschlauchung oder Webhook-Konfiguration keinen Lauf zulassen.

Für einen iPhone-Kurzbefehl genügt die Aktion **Inhalte von URL abrufen**:

```text
POST http://NAS-IP:8080/api/manual-run
Content-Type: application/json

{"auto_weather":true}
```

Die Home-Assistant-Webhook-URL selbst ist nicht zum Öffnen in der Browser-Adresszeile gedacht. Ein Browser sendet dabei `GET`; der Webhook akzeptiert aus Sicherheitsgründen nur `POST` und startet dann keinen Pumpzyklus. Zum gezielten Testen des Webhooks im lokalen Netz:

```bash
curl -X POST http://HA-IP:8123/api/webhook/WEBHOOK-ID
```

Den Befehl entweder vollständig in einer Zeile eingeben oder bei einem Zeilenumbruch den Backslash als letztes Zeichen der Zeile setzen. `curl -X POST \ http://...` ist ungültig, weil dadurch ein Leerzeichen Bestandteil der URL wird.

Im normalen Betrieb immer den Planner-Endpunkt `POST http://NAS-IP:8080/api/manual-run` verwenden. Er prüft Tankstand und Verschlauchung, bevor er den HA-Webhook auslöst.

Nach einer Änderung an `HOME_ASSISTANT_WEBHOOK_URL` muss der Planner-Container neu erstellt werden. Ein normaler Neustart übernimmt die geänderte Compose-Umgebung nicht immer:

```bash
cd /volume1/docker/watering-planner
sudo docker compose -f docker-compose.yml up -d --build --force-recreate
sudo docker inspect watering-planner --format '{{range .Config.Env}}{{println .}}{{end}}' | grep HOME_ASSISTANT_WEBHOOK_URL
```

Der letzte Befehl muss die konfigurierte URL mit der festen HA-IP ausgeben.

## 6. Prüfen und neu laden

1. **Entwicklerwerkzeuge > YAML > Konfiguration prüfen**
2. Wenn gültig: Home Assistant vollständig neu starten. Das ist insbesondere nach dem erstmaligen Eintragen von `webhook:` erforderlich, damit der HTTP-Endpunkt `/api/webhook/...` geladen wird. Nach späteren Änderungen ausschließlich an Automationen reicht **Automationen neu laden**.
3. Unter **Einstellungen > Automatisierungen & Szenen** prüfen, ob `Bewaesserung - Tagesfenster` und `Bewaesserung - Manueller Sofortlauf` sichtbar und aktiviert sind.
4. Unter **Entwicklerwerkzeuge > Zustände** prüfen:

```text
sensor.bewaesserung_planner
switch.smart_plug_mini
```

5. Unter **Entwicklerwerkzeuge > Aktionen** prüfen:

```text
rest_command.bewaesserung_mark_run
script.bewaesserung_pumpzyklus
```

6. Den registrierten HA-Webhook direkt testen. Achtung: Bei Erfolg startet sofort ein vollständiger Pumpzyklus:

```bash
curl -i -X POST http://HA-IP:8123/api/webhook/WEBHOOK-ID
```

Ein `POST` mit `404: Not Found` bedeutet, dass der HTTP-Endpunkt `/api/webhook/...` in Home Assistant nicht geladen wurde. In diesem Fall prüfen:

- Steht `webhook:` als eigener Top-Level-Block in `/config/configuration.yaml`?
- Wurde Home Assistant danach vollständig neu gestartet? **Automationen neu laden** genügt für diesen ersten Schritt nicht.
- Wurde die aktualisierte `home-assistant/automations.yaml` wirklich nach `/config/automations.yaml` in Home Assistant kopiert?
- Ist `Bewaesserung - Manueller Sofortlauf` in Home Assistant sichtbar und aktiviert?
- Stimmt die im HA-Webhook-Trigger tatsächlich angezeigte ID exakt mit der getesteten URL überein?
- Meldet **Einstellungen > System > Protokolle** einen Fehler beim Einlesen der Automation?

Sobald der Endpunkt geladen ist, liefert aktuelles Home Assistant aus Sicherheitsgründen auch für eine unbekannte oder nicht erlaubte Webhook-ID eine neutrale Antwort. Deshalb anschließend in Home Assistant unter **Einstellungen > Automatisierungen & Szenen > Bewaesserung - Manueller Sofortlauf > Ablaufverfolgungen** und an der Steckdose prüfen, ob der Lauf wirklich ausgelöst wurde.

Wenn der Endpunkt geladen ist, aber der Lauf nicht startet, den Trigger einmal direkt in Home Assistant neu erzeugen:

1. Unter **Einstellungen > Automatisierungen & Szenen** die Automation **Bewaesserung - Manueller Sofortlauf** öffnen.
2. Den vorhandenen Webhook-Trigger entfernen und einen neuen Trigger vom Typ **Webhook** hinzufügen.
3. Die von Home Assistant neu erzeugte, zufällige Webhook-ID verwenden. Als Methode nur `POST` zulassen. **Nur aus dem lokalen Netzwerk erreichbar** zunächst deaktivieren. Abhängig von VM- und Netzwerk-Konfiguration erkennt Home Assistant eine Anfrage aus dem Heimnetz sonst möglicherweise nicht als lokal.
4. Automation speichern, aktivieren und die neu erzeugte URL erneut mit `curl -i -X POST ...` testen. Achtung: Ein erfolgreicher Test startet sofort die Pumpe.
5. Erst wenn dieser direkte Test funktioniert, dieselbe neue URL in `.env.synology` auf der NAS eintragen und den Planner-Container neu erstellen.

Die echte Webhook-ID nicht in die Projektvorlage, ein öffentliches Repository oder einen Chat kopieren. In `home-assistant/automations.yaml` des Projekts bleibt der Platzhalter `REPLACE_WITH_RANDOM_WEBHOOK_ID`; nur die aktive Automation in Home Assistant enthält die private ID.

Wenn die Ablaufverfolgung des manuellen Sofortlaufs vorhanden ist, aber die Pumpe nicht einschaltet, die Aktionskette von hinten nach vorne prüfen:

1. Unter **Entwicklerwerkzeuge > Aktionen** die Aktion `switch.turn_on` mit der tatsächlichen Entity-ID der Meross-Steckdose ausführen. Wenn die Steckdose nicht schaltet, ist die Entity-ID falsch, das Gerät nicht verfügbar oder die Matter-Einbindung gestört.
2. Falls die Steckdose direkt schaltet, dort die Aktion `script.bewaesserung_pumpzyklus` ausführen. Wenn sie nicht in der Liste erscheint, wurde der `script:`-Block aus `configuration.yaml` noch nicht geladen oder die Entity-ID des Skripts weicht ab.
3. Falls auch das Skript direkt funktioniert, die Webhook-ID in der aktiven Automation mit der URL in `.env.synology` vergleichen und den manuellen Webhook-Trigger neu speichern.

Die Vorlage verwendet `switch.smart_plug_mini` nur als Beispiel. Wenn Home Assistant eine andere Entity-ID vergeben hat, muss sie in `configuration.yaml` bei **Pumpe einschalten** und **Pumpe ausschalten** ersetzt werden.

7. Prüfen, ob der laufende Planner den manuellen Lauf freigibt:

```bash
curl -s "http://NAS-IP:8080/api/homekit/check?auto=true" | grep -A4 '"manual_run"'
```

Erwartet wird `"available": true`. Bei `"available": false` steht direkt darunter der konkrete Sperrgrund.

## 7. Sicherheit

Die Pumpe sollte standardmäßig aus sein. Ein Lauf ist:

```text
Steckdose an -> 120 Sekunden warten -> Steckdose aus -> Lauf verbuchen
```

Falls Home Assistant oder der Planer nicht erreichbar ist, startet kein neuer Lauf. Das ist sicherer als eine dauerhaft eingeschaltete Steckdose, die jeden Tag ohne Rückfrage pumpt.
Der Endpunkt `/api/manual-run` ist für das lokale Heimnetz vorgesehen. Falls der Planer über einen Reverse Proxy aus dem Internet erreichbar ist, muss dieser Pfad dort zusätzlich geschützt werden.
Die HA-Automation nutzt für den Webhook `local_only: false`, weil Home Assistant eine Anfrage aus dem Planner-Container abhängig von VM- und Netzwerk-Konfiguration möglicherweise nicht als lokal erkennt. Behandle die zufällige Webhook-ID deshalb wie ein Passwort. Veröffentliche sie nicht und erzeuge eine neue ID, wenn du das Projekt teilst oder die URL versehentlich offengelegt wurde.

## 8. Tests nach Einrichtung

1. In Home Assistant die Steckdose manuell kurz einschalten und wieder ausschalten.
2. Im Planer manuell Wetterwerte setzen und prüfen, ob ein Zyklus ansteht.
3. In Home Assistant den Sensor `sensor.bewaesserung_planner` aktualisieren.
4. Automation einmal manuell ausführen.
5. Prüfen, ob im Planer `cycles_completed_today` um 1 steigt.

## 9. Fehlersuche

- Sensor bleibt `unavailable`: `NAS-IP` aus der HA-VM heraus prüfen.
- Steckdose schaltet nicht: Entity-ID kontrollieren.
- Lauf wird nicht verbucht: `rest_command` URL und Logs prüfen.
- Button **Zyklus jetzt starten** bleibt deaktiviert: `HOME_ASSISTANT_WEBHOOK_URL` in `.env.synology`, Tankstand und Verschlauchung prüfen.
- Button bleibt nach Eintragen des Webhooks deaktiviert: Planner-Container mit `--force-recreate` neu erstellen und die laufende Container-Umgebung mit `docker inspect` kontrollieren.
- Direkter Aufruf der HA-Webhook-URL im Browser startet keinen Lauf: normal, weil der Browser `GET` statt `POST` verwendet. Mit `curl -X POST` testen.
- Direkter HA-Webhook-Test mit `POST` liefert `404`: `webhook:` als Top-Level-Block in `/config/configuration.yaml` ergänzen und Home Assistant vollständig neu starten.
- Direkter HA-Webhook-Test liefert keine `404`, startet aber keinen Lauf: Ablaufverfolgung prüfen und den Webhook-Trigger bei Bedarf in der Home-Assistant-Oberfläche neu erzeugen. Danach die neue private URL in `.env.synology` übernehmen.
- Web-Button meldet, dass Home Assistant nicht erreichbar ist: in `HOME_ASSISTANT_WEBHOOK_URL` eine feste HA-IP statt `homeassistant.local` verwenden.
- Matter-Gerät lässt sich nicht hinzufügen: Gerät aus Apple Home per Multi-Admin teilen und Home Assistant Companion App verwenden.
- Automation erscheint nicht im GUI: prüfen, ob `automation: !include automations.yaml` in `configuration.yaml` steht und die Automation in `/config/automations.yaml` mit `- id:` beginnt.
