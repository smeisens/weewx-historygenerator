#
# Copyright (c) 2013-2016  Nick Dajda <nick.dajda@gmail.com>
#
# Modifications (c) 2026  Sigi
#   - standalone WeeWX extension, bug fixes and optimizations
#   - based on bin/user/historygenerator.py from fuzzy-archer
#     https://github.com/brewster76/fuzzy-archer
#
# Distributed under the terms of the GNU GENERAL PUBLIC LICENSE
#
"""Extends the Cheetah generator search list to add html historic data tables in a nice color scheme.

Works with all databases.
Observes the units of measure and display formats specified in skin.conf.

Requires WeeWX 4 or later (Python 3).

To use it, add this generator to search_list_extensions in skin.conf:

[CheetahGenerator]
    search_list_extensions = user.historygenerator.MyXSearch, user.historygenerator.FormatCell

1) The $alltime tag:

Allows tags such as $alltime.outTemp.max for the all-time max temperature.
It uses the data binding given in [HistoryReport] (option data_binding),
default wx_binding.

2) Nice colorful tables summarising history data by month and year

Check out the preconfigured skin.conf [HistoryReport] and [HistoryColors] sections.
"""

import bisect
import logging
import math
import os.path
import time
from datetime import datetime

import weewx.units
import weeutil.weeutil
from weewx.cheetahgenerator import SearchList
from weewx.tags import TimespanBinder

try:
    from weeutil.weeutil import accumulateLeaves
except ImportError:
    from weeutil.config import accumulateLeaves

__version__ = "1.0.0"

log = logging.getLogger(__name__)

_FILE = os.path.basename(__file__)

DEFAULT_BINDING = 'wx_binding'
DEFAULT_FONT_COLOR = '#000000'
DEFAULT_BACKGROUND = 'transparent'
EMPTY_CELL = '&nbsp;'

THRESHOLD_AGGREGATES = frozenset([
    'max_ge', 'max_le', 'min_ge', 'min_le',
    'sum_ge', 'sum_le', 'avg_ge', 'avg_le',
    'avg_gt', 'avg_lt',
])

# skin.conf sections that are made available to the templates
EXTENSION_SECTIONS = (
    'Navigation', 'StationInfo', 'TranslationLinks', 'HistoryReport',
    'ImageGenerator', 'BootstrapLabels', 'Labels', 'Units', 'JSONGenerator',
    'LiveGauges', 'Stats', 'News', 'LiveCharts', 'locale',
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _as_list(value):
    """Return a config option as list (configobj returns a str for single values)."""
    if value is None:
        return []
    return weeutil.weeutil.option_as_list(value)


def _count_tuple(n=0):
    return weewx.units.ValueTuple(n, 'count', 'group_count')


def _parse_scale(config, label):
    """Parse one color scale (minvalues/colors/fontColors) into sorted lists.

    Returns (bounds, colors, font_colors) or None if the definition is invalid.
    """
    try:
        bounds = [float(v) for v in _as_list(config['minvalues'])]
        colors = _as_list(config['colors'])
    except (KeyError, ValueError, TypeError):
        log.warning("%s: invalid or missing minvalues/colors in [HistoryColors]%s", _FILE, label)
        return None

    font_colors = _as_list(config.get('fontColors')) or [DEFAULT_FONT_COLOR] * len(bounds)

    if not (len(bounds) == len(colors) == len(font_colors)) or not bounds:
        log.warning("%s: minvalues, colors and fontColors must have the same number of elements "
                    "in [HistoryColors]%s", _FILE, label)
        return None

    # bisect needs ascending bounds; keep colors aligned
    entries = sorted(zip(bounds, colors, font_colors), key=lambda e: e[0])
    return ([e[0] for e in entries], [e[1] for e in entries], [e[2] for e in entries])


def build_color_scales(color_dict, colors_key):
    """Pre-parse all color scales of one [HistoryColors] entry, keyed by unit.

    Result: {unit: {"main": scale, "summary": scale}}
    """
    scales = {}
    if not color_dict or colors_key not in color_dict:
        return scales

    for unit, config in color_dict[colors_key].items():
        if not hasattr(config, 'keys'):
            continue
        label = "[[%s]][[[%s]]]" % (colors_key, unit)
        main = _parse_scale(config, label)
        if main is None:
            continue
        summary = None
        if 'summary' in config:
            summary = _parse_scale(config['summary'], label + "[[[[summary]]]]")
        scales[unit] = {"main": main, "summary": summary or main}
    return scales


def _lookup_color(scale, value):
    """Return (background, font) for value, or None if below the lowest bound."""
    bounds, colors, font_colors = scale
    index = bisect.bisect_right(bounds, value) - 1
    if index < 0:
        return None
    return colors[index], font_colors[index]


def _covers(period_timespan, alltime_timespan):
    return (period_timespan.start >= alltime_timespan.start
            and period_timespan.stop <= alltime_timespan.stop)


# ---------------------------------------------------------------------------
# Search list extension
# ---------------------------------------------------------------------------

class MyXSearch(SearchList):
    def __init__(self, generator):
        SearchList.__init__(self, generator)

        skin_dict = generator.skin_dict
        self.table_dict = skin_dict['HistoryReport']
        self.color_dict = skin_dict.get('HistoryColors', {})
        self.units_dict = skin_dict.get('Units', {})

        # Calculate the tables once every refresh_interval mins
        self.refresh_interval = int(self.table_dict.get('refresh_interval', 5))
        self.cache_time = 0

        ext = {}
        ext['fuzzy_archer_version'] = skin_dict.get('version', '')
        self.search_list_extension = ext

        weewx_root = generator.config_dict['WEEWX_ROOT']
        html_root = skin_dict['HTML_ROOT']
        ext['css_mtime'] = self.last_mtime_resources('css', weewx_root, html_root)
        ext['js_mtime'] = self.last_mtime_resources('js', weewx_root, html_root)

        # Make some config available to templates
        for key in EXTENSION_SECTIONS:
            if key in skin_dict:
                ext[key] = skin_dict[key]
            else:
                log.debug("%s: No [%s] section found in skin.conf", _FILE, key)

    def get_extension_list(self, valid_timespan, db_lookup):
        """Return a list of objects whose attributes or keys define the extension.

        valid_timespan: weeutil.weeutil.TimeSpan with the domain of valid times.
        db_lookup: function db_lookup(data_binding) returning a database manager.
        """
        now = time.time()
        if now - self.refresh_interval * 60 <= self.cache_time:
            return [self.search_list_extension]
        self.cache_time = now

        t1 = time.time()

        # Query first/last timestamp only once per binding.
        timespans = {}

        def alltime_timespan(binding):
            if binding not in timespans:
                manager = db_lookup(data_binding=binding)
                first, last = manager.first_timestamp, manager.last_timestamp
                timespans[binding] = (weeutil.weeutil.TimeSpan(first, last)
                                      if first is not None and last is not None else None)
            return timespans[binding]

        # $alltime: one well-defined binding instead of "whatever the last table used"
        default_binding = self.table_dict.get('data_binding', DEFAULT_BINDING)
        try:
            ts = alltime_timespan(default_binding)
            if ts is not None:
                self.search_list_extension['alltime'] = TimespanBinder(
                    ts, db_lookup, data_binding=default_binding,
                    formatter=self.generator.formatter, converter=self.generator.converter)
        except Exception as e:
            log.error("%s: could not create $alltime for binding %s: %s", _FILE, default_binding, e)

        tables = {}
        ngen = 0
        for table_key in self.table_dict.sections:
            try:
                table = self._build_table(table_key, db_lookup, alltime_timespan)
            except Exception as e:
                # One broken table must not kill the whole report
                log.error("%s: could not generate table [HistoryReport][[%s]]: %s", _FILE, table_key, e)
                table = {"year": [], "settings": {"table_options": {}, "color_dict": self.color_dict,
                                                  "error": str(e)}}
            if table is not None:
                tables[table_key] = table
                if "error" not in table["settings"]:
                    ngen += 1

        self.search_list_extension["history_tables"] = tables

        log.info("%s: Generated %d tables in %.2f seconds", _FILE, ngen, time.time() - t1)

        return [self.search_list_extension]

    # -----------------------------------------------------------------------

    def _build_table(self, table_key, db_lookup, alltime_timespan):
        table_options = accumulateLeaves(self.table_dict[table_key])
        binding = table_options.get('data_binding', DEFAULT_BINDING)

        alltime_ts = alltime_timespan(binding)
        if alltime_ts is None:
            log.warning("%s: no data in binding %s for [HistoryReport][[%s]]", _FILE, binding, table_key)
            return None

        # Show all time unless starting date specified
        startdate = table_options.get('startdate')
        if startdate is not None:
            start = weeutil.weeutil.startOfDay(int(startdate))
            table_ts = weeutil.weeutil.TimeSpan(start, alltime_ts.stop)
        else:
            table_ts = alltime_ts

        table_stats = TimespanBinder(table_ts, db_lookup, data_binding=binding,
                                     formatter=self.generator.formatter,
                                     converter=self.generator.converter)

        table = {
            "year": [],
            "settings": {"table_options": table_options, "color_dict": self.color_dict},
        }

        if table_key == 'NOAA':
            self._fill_noaa(table, table_options, table_stats)
        else:
            self._fill_stats(table_key, table, table_options, table_stats, binding, alltime_ts)
        return table

    def _fill_stats(self, table_key, table, table_options, table_stats, binding, alltime_ts):
        settings = table["settings"]
        for option in ('summary_column', 'alltime_min', 'alltime_avg', 'alltime_max'):
            settings[option] = weeutil.weeutil.to_bool(table_options.get(option, False))

        table_name = table_key + '_table'
        settings["table_name"] = table_name
        table["header_text"] = table_options.get("header_text", table_name + "header_text")

        obs_type = table_options['obs_type']
        aggregate_type = table_options['aggregate_type']

        # --- colors (parsed once per table instead of once per cell) ---
        colors_option = _as_list(table_options.get('colors'))
        colors_key = colors_option[0] if colors_option else obs_type
        settings["colors_key"] = colors_key
        settings["color_scales"] = build_color_scales(self.color_dict, colors_key)

        display_unit = colors_option[1] if len(colors_option) > 1 else \
            self.units_dict.get('Groups', {}).get(weewx.units.obs_group_dict.get(obs_type))
        if display_unit and display_unit not in settings["color_scales"]:
            log.info("%s: no colors defined for [HistoryReport][[%s]] (unit %s)", _FILE, table_key, display_unit)

        # --- threshold aggregates like max_ge ---
        threshold = None
        if aggregate_type in THRESHOLD_AGGREGATES:
            try:
                raw = _as_list(table_options['aggregate_threshold'])
                threshold = (float(raw[0]), raw[1], weewx.units.obs_group_dict[obs_type])
            except (KeyError, IndexError, ValueError, TypeError):
                raise ValueError("aggregate_threshold must be in the format: value, unit")

        # --- determine unit and format from one all-time reading ---
        aggregate = getattr(getattr(table_stats, obs_type), aggregate_type)
        reading = aggregate(threshold) if threshold is not None else aggregate
        unit_type = reading.converter.group_unit_dict.get(reading.value_t[2])

        if threshold is None and unit_type is None:
            raise ValueError("no unit found for obs_type %s, check units" % obs_type)

        if threshold is not None or unit_type is None:
            format_string = '%d'
        else:
            format_string = reading.formatter.unit_format_dict[unit_type]
        settings["format_string"] = format_string

        table["unit"] = table_options.get('units') or reading.formatter.unit_label_dict.get(unit_type, '')

        # --- values per month / year ---
        converter = self.generator.converter
        for year in table_stats.years():
            year_data = {
                "head": {"value": str(datetime.fromtimestamp(year.timespan.start).year)},
                "values": [],
                "sum": None, "count": 0, "min": None, "max": None, "avg": None,
            }
            for month in year.months():
                year_data["values"].append({
                    "value": self._period_value(month, obs_type, aggregate_type, threshold, binding, converter),
                    "covers_timespan": _covers(month.timespan, alltime_ts),
                })
            year_data["summary"] = {
                "value": self._period_value(year, obs_type, aggregate_type, threshold, binding, converter),
                "covers_timespan": _covers(year.timespan, alltime_ts),
            }
            table["year"].append(year_data)

        self._aggregations(table, aggregate_type)

    @staticmethod
    def _period_value(period, obs_type, aggregate_type, threshold, binding, converter):
        obs = getattr(period, obs_type)
        obs.data_binding = binding
        if threshold is not None:
            try:
                return getattr(obs, aggregate_type)(threshold).value_t
            except Exception as e:
                log.debug("%s: %s(%s) failed: %s", _FILE, aggregate_type, obs_type, e)
                return _count_tuple(0)
        return converter.convert(getattr(obs, aggregate_type).value_t)

    # --- all-time min/avg/max row ------------------------------------------

    def _aggregations(self, table, aggregate_type):
        settings = table["settings"]
        wanted = [k for k in ('min', 'avg', 'max') if settings["alltime_" + k]]
        if not wanted:
            return

        def new_entry():
            entry = {"sum": None, "count": 0}
            entry.update({k: None for k in wanted})
            return entry

        summary_entry = new_entry()
        month_entries = []
        table["aggregations"] = {"head": aggregate_type, "values": month_entries, "summary": summary_entry}

        for year in table["year"]:
            for index, month in enumerate(year["values"]):
                if index >= len(month_entries):
                    month_entries.append(new_entry())
                self._aggregate(month["value"], month_entries[index], aggregate_type, month["covers_timespan"])
            self._aggregate(year["summary"]["value"], summary_entry, aggregate_type,
                            year["summary"]["covers_timespan"])

        for entry in month_entries + [summary_entry]:
            if "avg" in entry and entry["count"] > 0:
                s = entry["sum"]
                entry["avg"] = weewx.units.ValueTuple(s.value / entry["count"], s.unit, s.group)

    @staticmethod
    def _aggregate(value, entry, aggregate_type, covers_timespan):
        if value is None or value[0] is None:
            return
        if not isinstance(value, weewx.units.ValueTuple):
            value = weewx.units.ValueTuple(*value)

        if covers_timespan:
            entry["count"] += 1
            s = entry["sum"]
            entry["sum"] = value if s is None else weewx.units.ValueTuple(s.value + value.value, s.unit, s.group)
            update_min = update_max = True
        else:
            # Incomplete periods only count for absolute extremes
            update_min = aggregate_type == "min"
            update_max = aggregate_type == "max"

        if update_min and "min" in entry and (entry["min"] is None or value.value < entry["min"].value):
            entry["min"] = value
        if update_max and "max" in entry and (entry["max"] is None or value.value > entry["max"].value):
            entry["max"] = value

    # --- NOAA ---------------------------------------------------------------

    @staticmethod
    def _fill_noaa(table, table_options, table_stats):
        table["header_text"] = "NOAA_tableheader_text"
        table["settings"]["noaa"] = True
        stats_ts = table_stats.timespan

        for year in table_stats.years():
            dt_year = datetime.fromtimestamp(year.timespan.start)
            head = {"value": str(dt_year.year), "url": dt_year.strftime(table_options['year_filename'])}
            year_data = {"head": head, "values": [], "sum": 0, "count": 0, "min": None, "max": None}

            for month in year.months():
                cell = {"value": ""}
                if month.timespan.stop >= stats_ts.start and month.timespan.start <= stats_ts.stop:
                    dt = datetime.fromtimestamp(month.timespan.start)
                    cell["value"] = dt.strftime("&nbsp;&nbsp;%b&nbsp;'%y")
                    cell["url"] = dt.strftime(table_options['month_filename'])
                year_data["values"].append(cell)

            year_data["summary"] = head
            table["year"].append(year_data)

    # --- cache busting for css/js ------------------------------------------

    def last_mtime_resources(self, resource_type, weewx_root, html_root):
        if not os.path.isabs(html_root):
            html_root = os.path.join(weewx_root, html_root)
        resource_path = os.path.join(html_root, resource_type)
        if not os.path.isdir(resource_path):
            log.debug("%s: couldn't open %s, using version instead", _FILE, resource_path)
            return self.search_list_extension['fuzzy_archer_version']

        last_mtime = 0
        with os.scandir(resource_path) as entries:
            for entry in entries:
                if entry.name.endswith(resource_type) and entry.is_file():
                    last_mtime = max(last_mtime, entry.stat().st_mtime)
        return str(math.floor(last_mtime))


# ---------------------------------------------------------------------------
# Cell formatting, called from the templates via $format_cell(...)
# ---------------------------------------------------------------------------

class FormatCell(SearchList):
    def format_cell(self, value, table_settings, summary=False):
        result = {"formatted_value": None, "font_color": DEFAULT_FONT_COLOR,
                  "background_color": DEFAULT_BACKGROUND}

        if value is None:
            return result
        if not isinstance(value, weewx.units.ValueTuple):
            # legacy [0, 'count'] lists
            items = list(value) + [None] * 3
            value = weewx.units.ValueTuple(items[0], items[1], items[2] or 'group_count')

        result["formatted_value"] = weewx.units.ValueHelper(value).format(
            table_settings["format_string"], EMPTY_CELL)

        if value.value is None:
            return result

        scales = table_settings.get("color_scales")
        if scales is None:
            # table settings created by an older generator version
            options = table_settings["table_options"]
            colors_option = _as_list(options.get("colors"))
            colors_key = colors_option[0] if colors_option else options["obs_type"]
            scales = build_color_scales(table_settings["color_dict"], colors_key)
            table_settings["color_scales"] = scales

        scale = scales.get(value.unit)
        if scale is None:
            return result

        hit = _lookup_color(scale["summary" if summary else "main"], value.value)
        if hit is not None:
            result["background_color"], result["font_color"] = hit
        return result
