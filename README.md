# MediTrack

MediTrack speichert Fiebermessungen und Medikamentengaben pro Kind. Medikamente und ihre Regeln werden pro Kind angelegt und können später bearbeitet werden.

## In Home Assistant installieren

Voraussetzung ist eine Home-Assistant-Installation mit Supervisor und App-Store (zum Beispiel Home Assistant OS).

1. Sobald die Projektdateien auf GitHub liegen, in Home Assistant **Einstellungen → Apps → App-Store** öffnen.
2. Im Menü **⋮ → Repositories** die Adresse `https://github.com/Alpenjugo/meditrack` hinzufügen. Der Supervisor muss das Repository lesen können.
3. **MediTrack** installieren, starten und **In Seitenleiste anzeigen** aktivieren.
4. MediTrack in der Seitenleiste öffnen. Der Zugriff läuft über die bestehende Home-Assistant-Anmeldung und ist auf Administratoren beschränkt.

Die App speichert die Einträge lokal in ihrer SQLite-Datenbank unter `/data`. Für die Datensicherung MediTrack in das Home-Assistant-Backup aufnehmen. Die Zeitzone kann in der App-Konfiguration geändert werden. Standard ist `Europe/Zurich`.

## Einträge und Regeln

- Ein Kind per Name oder Kürzel anlegen.
- Für das Kind ein Medikament mit Mindestabstand in Stunden (z. B. 1,5 für 90 Minuten, minutengenau gerundet) und maximaler Anzahl Gaben pro Kalendertag erfassen.
- Im **globalen Medikamentenkatalog** kannst du Medikamente einmal anlegen. Unter **Medikament hinzufügen** übernimmst du die Vorgaben für das ausgewählte Kind. Spätere Änderungen am Katalog gelten für künftige Übernahmen.
- Hinterlege optional Wirkstoff, Darreichungsform und eine dauerhafte Notiz zum Medikament. Die Wirkstoff-Vorlagen im Katalog füllen nur den Namen und Wirkstoff aus. Darreichungsform und Einnahmeregeln trägst du passend zum konkreten Präparat ein.
- Temperatur oder Medikamentengabe direkt zum aktuellen Zeitpunkt mit optionaler Notiz erfassen. Die letzten 50 Einträge pro Kind erscheinen im Verlauf.
- Bei erreichtem Tageslimit oder zu kurzem Abstand blockiert MediTrack weitere Gaben-Einträge und zeigt den nächsten möglichen Zeitpunkt an. Das Tageslimit wird um Mitternacht in der eingestellten Zeitzone zurückgesetzt. Der Mindestabstand gilt auch über Mitternacht hinweg.

Die Regeln stammen aus deinen Eingaben. MediTrack berechnet keine Dosierung.

## Lokal entwickeln

Mit Python 3.12 und der System-Zeitzonendatenbank aus dem Repository starten. Zusätzliche Python-Pakete sind nicht erforderlich.

```sh
python3 meditrack/app.py --local --port 8099
```

Die Entwicklungsansicht ist auf Loopback unter Port 8099 erreichbar. Mit `--database` lässt sich eine separate Testdatenbank angeben. Die Anwendungstests starten einen lokalen Server mit einer temporären Datenbank.

```sh
python3 -m unittest discover -s meditrack -v
```
