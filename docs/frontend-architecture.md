# Frontend-Architektur

`public/app.js` ist nur noch Einstiegspunkt und Orchestrierung. Die Oberfläche
verwendet native Browser-APIs und keine CDN- oder Laufzeitabhängigkeiten.

## Module

- `js/api.js`, `store.js`: HTTP-Zugriff und globaler Zustand
- `js/navigation.js`: hashbasierte Ansichten und Fokuswechsel
- `js/dashboard.js`: Hauptaktion, Tageszeitleiste und kompakte Statuswerte
- `js/forecast.js`: SVG-Tankverlauf und barrierearme 16-Tage-Liste
- `js/plants.js`: Versorgung, Filter, Anschlussaktionen und Pflanzenformulare
- `js/hoses.js`: responsive Bearbeitung und lokale Validierung
- `js/settings.js`: Konfiguration, Zeitvorschau, Kalibrierung und Balkonplan
- `js/diagnostics.js`: Ampelstatus und Benachrichtigungsprotokoll
- `js/history.js`: separates Anlagenprotokoll
- `js/ui.js`, `format.js`: lokale Icons, Dialoge, Toasts und Formatierung
- `js/updater.js`: Updaterstatus und geheime Token-Eingabe

Das CSS ist in Tokens, Basis, Layout, Komponenten, Ansichten und responsive
Regeln getrennt. Dynamische und nutzerdefinierte Inhalte werden mit
`textContent` oder erzeugten DOM-Knoten dargestellt. Es gibt keine Zuweisung an
`innerHTML`, keine nativen `alert`-/`confirm`-Dialoge und keine externen Assets.

## Bedienänderungen

- **Heute** beginnt mit genau einer priorisierten Aktion. Darunter stehen
  Tageszeitpunkte und vier Statusbereiche; das Ereignisprotokoll liegt unter
  **Verlauf**.
- **Prognose** zeigt Haupt- und Vorratstank, Bewässerungs- und
  Nachfüllmarkierungen, den ersten Ausfall und extrapolierte Bereiche.
- **Pflanzen** zeigt Bedarf gegen tatsächliche Tagesversorgung. Modellwerte
  sind eingeklappt; Anschlussaktionen sind fachlich benannt und ignorierte
  Empfehlungen können wieder eingeblendet werden.
- **Schläuche** ist auf Desktop eine Tabelle und auf Mobilgeräten eine
  Kartenliste. Zuordnung und Ausgang werden direkt editiert.
- **Setup** gruppiert Zeitplan, Tanks, Nachfüllung, Standort, Kalibrierung und
  Benachrichtigungen. Balkonplan und Technikwerte sind standardmäßig
  eingeklappt.
- **System** enthält Wetter, Datenalter, Home Assistant, beide Automatiken,
  SMTP, Datenbank und Updater mit jeweils höchstens einer passenden Aktion.
- Löschen verwendet zugängliche Bestätigungsdialoge und bietet anschließend
  eine zeitlich begrenzte Aktion **Rückgängig**.

## Geprüfte Zustände

Desktop bei `1440 x 1000`: feste linke Navigation, dominante Handlungszeile,
Tageszeitleiste und vier Statuskarten in einer Reihe. Der geprüfte
Wetterfehler zeigt sofort **Wetterdaten aktualisieren** und **Neu laden**.

Mobil bei `390 x 844`: kompakter Kopf, sichere untere Navigation,
zweizeilige Hauptaktion, horizontal scrollbare Tagespunkte und einspaltige
Statuskarten. Safe Areas, 44-Pixel-Touchziele und reduzierte Bewegung werden
berücksichtigt. Das Prognose-SVG scrollt innerhalb seines Bereichs und erzeugt
keinen Seitenüberlauf.

## Tests und Grenzen

- Python-Strukturtests prüfen Module, PWA-Cache, Ansichten, sichere
  Textdarstellung, Touch-/Safe-Area-Regeln und lokale Assets.
- Node-Modelltests prüfen Navigation, Dashboardprioritäten, Zeitleiste,
  Prognosedaten, Zeitfenster, Pflanzenfilter, Schlauchwarnungen, Diagnose und
  Escaping.
- Ein isolierter Headless-Edge-Lauf hat Desktop und Mobil ohne
  JavaScript-Ladefehler gerendert; alle 27 PWA-Shell-Assets antworteten mit 200.
- SMTP und externe Home-Assistant-/Open-Meteo-Verbindungen hängen weiterhin
  vom Zielnetz ab. SMTP-Verhalten ist unitgetestet, aber nicht gegen einen
  echten Mailserver versendet worden.
- Auf sehr kleinen Displays bleibt die achtteilige untere Navigation
  horizontal scrollbar. Die Diagrammspur scrollt ebenfalls horizontal, die
  vollständige Tagesliste bleibt darunter ohne Diagrammbedienung verfügbar.
