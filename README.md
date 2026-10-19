# weewx-historygenerator

Eigenständige WeeWX-Extension für farbige Monats-/Jahres-Verlaufstabellen und den `$alltime`-Tag in Cheetah-Skins.

Basiert auf `bin/user/historygenerator.py` aus dem Skin [fuzzy-archer](https://github.com/brewster76/fuzzy-archer) von Nick Dajda, als einzelne Datei herausgelöst, fehlerbereinigt und optimiert.

## Voraussetzungen

- WeeWX 4 oder 5 (Python 3)
- ein Skin, dessen Templates `$history_tables` und `$format_cell(...)` nutzen

## Installation

WeeWX 5
```
weectl extension install https://github.com/smeisens/weewx-historygenerator/archive/master.zip
``` 
oder direkt aus dem Ordner
``` 
weectl extension install /pfad/zu/weewx-historygenerator
```

WeeWX 4: `wee_extension --install weewx-historygenerator.zip`

Danach WeeWX neu starten.

> **Achtung:** fuzzy-archer installiert eine eigene `bin/user/historygenerator.py`. Wer beide Extensions installiert, überschreibt die Datei der jeweils anderen.

## Einbindung in den Skin

Die Extension installiert nur den Code. Die Konfiguration gehört in die `skin.conf` deines Skins:

```ini
[CheetahGenerator]
    search_list_extensions = user.historygenerator.MyXSearch, user.historygenerator.FormatCell

[HistoryReport]
    refresh_interval = 60       # Minuten zwischen Neuberechnungen
    # data_binding = wx_binding # Binding für $alltime (Default: wx_binding)

    [[min_temp]]
        obs_type = outTemp
        aggregate_type = min

    [[rain]]
        obs_type = rain
        aggregate_type = sum
        summary_column = true

    [[hot_days]]
        obs_type = outTemp
        aggregate_type = max_ge
        aggregate_threshold = 30, degree_C

[HistoryColors]
    [[outTemp]]
        [[[degree_C]]]
            minvalues  = -50, -10, 0, 10, 20, 30
            colors     = "#0029E5", "#02E3EA", "#05EF3D", "#E9F70A", "#F9A90B", "#FF0F2D"
            fontColors = "#ffffff", "#000000", "#000000", "#000000", "#000000", "#ffffff"
    [[rain]]
        [[[mm]]]
            minvalues = 0, 25, 50, 100
            colors    = "#E0F8E0", "#A9F5A9", "#58FA58", "#01DF01"
            [[[[summary]]]]
                minvalues = 0, 300, 600, 900
                colors    = "#E0F8E0", "#A9F5A9", "#58FA58", "#01DF01"
```

### Tabellenoptionen (`[HistoryReport][[name]]`)

| Option | Bedeutung |
|---|---|
| `obs_type` | Messgröße, z. B. `outTemp`, `rain`, `barometer` |
| `aggregate_type` | `min`, `max`, `avg`, `sum`, `count` oder mit Schwellwert: `max_ge`, `max_le`, `min_ge`, `min_le`, `sum_ge`, `sum_le`, `avg_ge`, `avg_le`, `avg_gt`, `avg_lt` |
| `aggregate_threshold` | Schwellwert und Einheit, z. B. `30, degree_C` |
| `data_binding` | Datenbank-Binding (Default `wx_binding`) |
| `startdate` | Unix-Timestamp, ab dem die Tabelle beginnt |
| `colors` | anderen Farbsatz verwenden: `<key>, <unit>` |
| `units` | eigene Einheitenbeschriftung |
| `header_text` | Überschrift-Label |
| `summary_column` | Jahres-Summary-Spalte (`true`/`false`) |
| `alltime_min`, `alltime_avg`, `alltime_max` | Zeile mit All-Time-Werten je Monat |

### Farben (`[HistoryColors]`)

Je Messgröße und **Einheit** eine Skala. `minvalues` sind Untergrenzen, die Obergrenze ist jeweils der nächste Wert. `fontColors` ist optional (Default Schwarz). Eine eigene Skala für die Summary-Spalte geht über eine `[[[[summary]]]]`-Untersektion.

## Unterschiede zum Original

- Summary-Spalte nutzt Farben und Schriftfarben der eigenen Summary-Skala
- `fontColors` optional
- Schwellwert-Aggregate liefern bei Fehlern `0` statt eines Absturzes im Template
- `$alltime` hängt fest am Binding aus `[HistoryReport]`
- Farbskalen werden einmal pro Tabelle geparst, Datenbank-Zeitspannen einmal pro Binding abgefragt
- eine fehlerhafte Tabelle bricht nicht mehr den ganzen Report ab
- fehlende `[HistoryColors]`-Sektion führt nur zu ungefärbten Tabellen

## Lizenz

GNU General Public License v3, siehe [LICENSE](LICENSE).

Copyright (c) 2013–2016 Nick Dajda, Änderungen (c) 2026 Sigi.
