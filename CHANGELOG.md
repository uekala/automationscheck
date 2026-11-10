# Changelog

Alle nennenswerten Änderungen an Automationscheck. Versionen folgen [Semantic Versioning](https://semver.org/lang/de/).

## Unveröffentlicht

### Behoben

- Kommandozeile: Eine fehlende oder unlesbare Datei und ungültiges JSON ergeben jetzt eine klare Fehlermeldung (Exit-Code 2) statt eines Python-Tracebacks.

### Tests

- Tests für die Kommandozeile (`engine/cli.py`): Laden von Exporten als Liste oder Objekt, Namensauflösung, Optionen, alle Befundarten im Bericht, „Viele Köche“, der dokumentierte Aufruf `python -m engine.cli` und Fehlerfälle.
- Tests für die Diagnosedaten über die echte Diagnose-Schnittstelle von Home Assistant, auch vor der ersten Prüfung.
- Frühwarntest, ob Home Assistant die Automationskonfiguration noch als `raw_config` bereitstellt, und Test, dass nicht lesbare Automationen als „übersprungen“ gemeldet werden statt die Prüfung abzubrechen.
- Abdeckung von 76 % auf 85 %.

## 0.1.0 – 2026-11-03

Erste Version.

- Findet Gegenspieler, Schleifen, Selbstauslösungen, unbekannte Trigger-IDs und Auslöser ohne Wirkung in Automationen und Skripten.
- Berücksichtigt Bedingungen, Trigger-Werte (`to`/`from`), Optionen von Auswahlfeldern, Wartezeiten, aufgerufene Skripte und Blueprints.
- Meldet Fehler und Warnungen als Reparaturen; Hinweise optional.
- Sensor mit Anzahl und Liste der Befunde, Aktion `automationscheck.run`, Diagnosedaten.
- Prüft nach dem Start, nach dem Speichern von Automationen oder Skripten und alle 6 Stunden.
- Verändert keine Automationen.
- Kommandozeile zur Analyse eines JSON-Exports.
