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

## 4. `configuration.yaml` vorbereiten

Die fertige Vorlage liegt unter [`home-assistant/configuration.yaml`](../home-assistant/configuration.yaml). Nach `/config/configuration.yaml` kopieren oder die enthaltenen Abschnitte in eine vorhandene Datei übernehmen. `NAS-IP` durch die IP der Synology ersetzen, zum Beispiel `192.168.178.116`.

Wichtig:

- `sensor:`, `rest_command:` und `automation:` stehen ganz links auf oberster YAML-Ebene.
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

Diese vollständige Automation nutzt die Meross-Steckdose `switch.smart_plug_mini`, schaltet sie pro Lauf 120 Sekunden ein und folgt der Entscheidung `run_now` aus dem Planer. Der Planer verteilt offene Zyklen über die Tagesfenster und zieht Läufe vor, wenn die verbleibenden Fenster knapp werden. Um 19 Uhr läuft nur noch ein Notfallzyklus, wenn der Planer `run_now` meldet.
Falls die Steckdose in Home Assistant eine andere Entity-ID erhalten hat, `switch.smart_plug_mini` in der Vorlage ersetzen.

Die Sensoraktualisierung muss vor der Bedingung stehen. Der zuvor gespeicherte Sensorzustand stammt sonst möglicherweise noch aus der Zeit zwischen zwei Fenstern und ist dort absichtlich `False`.

Der Planer verhindert Überbewässerung, weil nach jedem Lauf `remaining_cycles_today` sinkt.
Wenn sich Wetterdaten im Tagesverlauf verschärfen und dadurch mehr offene Zyklen entstehen, setzt der Planer `run_now` in den verbleibenden Zeitfenstern früher auf `true`. Home Assistant muss dadurch keine eigene Verteilungslogik kennen.

Im Dashboard des Planers wird zusätzlich angezeigt:

- ob Home Assistant jetzt starten darf
- nächstes Zeitfenster
- ob wegen knapper verbleibender Fenster vorgezogen wird
- ob die Automatik bis morgen pausiert ist
- Protokoll der letzten Bewässerungsvorgänge mit Zeitpunkt, Menge, Temperatur und Regen

Über die Buttons **Heute pausieren** und **Pause aufheben** kann die Automatik direkt im Planer gesperrt oder wieder freigegeben werden.

## 6. Prüfen und neu laden

1. **Entwicklerwerkzeuge > YAML > Konfiguration prüfen**
2. Wenn gültig: Home Assistant neu starten oder **Automationen neu laden**
3. Unter **Einstellungen > Automatisierungen & Szenen** prüfen, ob `Bewaesserung - Tagesfenster` sichtbar ist.
4. Unter **Entwicklerwerkzeuge > Zustände** prüfen:

```text
sensor.bewaesserung_planner
switch.smart_plug_mini
```

5. Unter **Entwicklerwerkzeuge > Aktionen** prüfen:

```text
rest_command.bewaesserung_mark_run
```

## 7. Sicherheit

Die Pumpe sollte standardmäßig aus sein. Ein Lauf ist:

```text
Steckdose an -> 120 Sekunden warten -> Steckdose aus -> Lauf verbuchen
```

Falls Home Assistant oder der Planer nicht erreichbar ist, startet kein neuer Lauf. Das ist sicherer als eine dauerhaft eingeschaltete Steckdose, die jeden Tag ohne Rückfrage pumpt.

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
- Matter-Gerät lässt sich nicht hinzufügen: Gerät aus Apple Home per Multi-Admin teilen und Home Assistant Companion App verwenden.
- Automation erscheint nicht im GUI: prüfen, ob `automation: !include automations.yaml` in `configuration.yaml` steht und die Automation in `/config/automations.yaml` mit `- id:` beginnt.
