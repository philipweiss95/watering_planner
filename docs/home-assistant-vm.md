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
compose.synology.yaml
server.py
public/
data/
```

Wenn deine `table.xlsx` Grundlage bleiben soll, liegt sie hier:

```text
/volume1/docker/watering-planner/data/table.xlsx
```

Container Manager:

1. **Container Manager > Projekt > Erstellen**
2. Projektpfad: `/volume1/docker/watering-planner`
3. Compose-Datei: `compose.synology.yaml`
4. Projekt starten

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

## 4. REST-Sensor für den Planer

In Home Assistant `configuration.yaml` ergänzen:

```yaml
sensor:
  - platform: rest
    name: Bewaesserung Planner
    unique_id: bewaesserung_planner
    resource: "http://NAS-IP:8080/api/homekit/check?auto=true&slot=morning"
    method: GET
    scan_interval: 900
    timeout: 10
    value_template: "{{ value_json.should_run }}"
    json_attributes:
      - should_run
      - reason
      - recommended_cycles_today
      - cycles_completed_today
      - remaining_cycles_today
      - pump
      - tank
```

Home Assistant neu starten. Danach gibt es einen Sensor:

```text
sensor.bewaesserung_planner
```

Der Sensor steht auf `True`, wenn ein Pumpenlauf ansteht.

## 5. REST-Command zum Verbuchen

Ebenfalls in `configuration.yaml`:

```yaml
rest_command:
  bewaesserung_mark_run:
    url: "http://NAS-IP:8080/api/homekit/mark-run"
    method: POST
    headers:
      Content-Type: "application/json"
    payload: '{"auto_weather": true, "slot": "morning"}'
```

## 6. Automation für einen einzelnen Lauf

Ersetze `switch.meross_pumpe` durch deine echte Steckdosen-Entity.

```yaml
alias: Bewaesserung - Zyklus starten wenn nötig
mode: single
trigger:
  - platform: time_pattern
    hours: "/2"
condition:
  - condition: state
    entity_id: sensor.bewaesserung_planner
    state: "True"
action:
  - service: switch.turn_on
    target:
      entity_id: switch.meross_pumpe
  - delay: "00:01:10"
  - service: switch.turn_off
    target:
      entity_id: switch.meross_pumpe
  - service: rest_command.bewaesserung_mark_run
  - service: homeassistant.update_entity
    target:
      entity_id: sensor.bewaesserung_planner
```

Diese Automation prüft alle zwei Stunden. Wenn noch ein Zyklus offen ist, wird genau ein Lauf gestartet und danach verbucht. Beim nächsten Zeitfenster wird erneut geprüft.

## 7. Mehr Kontrolle mit festen Zeitfenstern

Wenn du lieber feste Zeitfenster möchtest:

```yaml
trigger:
  - platform: time
    at: "07:00:00"
  - platform: time
    at: "11:00:00"
  - platform: time
    at: "15:00:00"
  - platform: time
    at: "19:00:00"
```

Der Planer verhindert Überbewässerung, weil nach jedem Lauf `remaining_cycles_today` sinkt.

## 8. Sicherheit

Die Pumpe sollte standardmäßig aus sein. Ein Lauf ist:

```text
Steckdose an -> 70 Sekunden warten -> Steckdose aus -> Lauf verbuchen
```

Falls Home Assistant oder der Planer nicht erreichbar ist, startet kein neuer Lauf. Das ist sicherer als eine dauerhaft eingeschaltete Steckdose, die jeden Tag ohne Rückfrage pumpt.

## 9. Tests nach Einrichtung

1. In Home Assistant die Steckdose manuell kurz einschalten und wieder ausschalten.
2. Im Planer manuell Wetterwerte setzen und prüfen, ob ein Zyklus ansteht.
3. In Home Assistant den Sensor `sensor.bewaesserung_planner` aktualisieren.
4. Automation einmal manuell ausführen.
5. Prüfen, ob im Planer `cycles_completed_today` um 1 steigt.

## 10. Fehlersuche

- Sensor bleibt `unavailable`: `NAS-IP` aus der HA-VM heraus prüfen.
- Steckdose schaltet nicht: Entity-ID kontrollieren.
- Lauf wird nicht verbucht: `rest_command` URL und Logs prüfen.
- Matter-Gerät lässt sich nicht hinzufügen: Gerät aus Apple Home per Multi-Admin teilen und Home Assistant Companion App verwenden.
