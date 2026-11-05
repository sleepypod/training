"""Wearables as sources of measurements to compare with the pod.

Devices differ in what they report and how, so every metric is optional: a device returns what it
has and a missing metric stays missing. Measurements are for comparison only. They never become
model features, because a pod model has to run on nights with no wearable.
"""
from contextlib import closing
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import re
from typing import Protocol
import xml.etree.ElementTree as ET

# Mirrors REFERENCE_SAMPLE_METRICS and REFERENCE_UNITS in sleepypod-core.
METRICS = {
    'breathing_rate': ('breaths/min', 4, 60),
    'hrv_sdnn': ('ms', 1, 500),
    'hrv_rmssd': ('ms', 1, 500),
    'blood_oxygen': ('%', 50, 100),
    'resting_heart_rate': ('bpm', 20, 250),
    'skin_temperature': ('degC', 20, 45),
    'breathing_disturbances': ('events/h', 0, 200),
}


def measurement(metric, value, start_ms, end_ms=None, device=None, algorithm=None):
    """One validated reading, or None when it is outside the metric's plausible range."""
    if metric not in METRICS:
        raise ValueError(f'Unknown metric: {metric}')
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{metric}: value must be a number')
    if not isinstance(start_ms, int) or (end_ms is not None and (not isinstance(end_ms, int) or end_ms < start_ms)):
        raise ValueError(f'{metric}: invalid time')
    _, low, high = METRICS[metric]
    if not low <= value <= high:
        return None
    return {'metric': metric, 'value': float(value), 'startMs': start_ms, 'endMs': None if end_ms == start_ms else end_ms,
            'device': device, 'algorithm': algorithm}


class ReferenceDevice(Protocol):
    """Reads what one wearable or export recorded. Anything it lacks is simply absent."""
    def measurements(self, path) -> list: ...


class AppleHealthExport:
    """Apple Health ZIP or XML, keeping only readings from sources matching `source`."""
    # Health holds blood oxygen as a fraction; the pod stores a percentage.
    TYPES = {
        'HKQuantityTypeIdentifierRespiratoryRate': ('breathing_rate', 1),
        'HKQuantityTypeIdentifierHeartRateVariabilitySDNN': ('hrv_sdnn', 1),
        'HKQuantityTypeIdentifierHeartRateVariabilityRMSSD': ('hrv_rmssd', 1),
        'HKQuantityTypeIdentifierOxygenSaturation': ('blood_oxygen', 100),
        'HKQuantityTypeIdentifierRestingHeartRate': ('resting_heart_rate', 1),
        'HKQuantityTypeIdentifierAppleSleepingWristTemperature': ('skin_temperature', 1),
        'HKQuantityTypeIdentifierAppleSleepingBreathingDisturbances': ('breathing_disturbances', 1),
    }

    def __init__(self, source='Watch'):
        if not source.strip():
            raise ValueError('Source filter must not be empty')
        self.source = source

    def measurements(self, path):
        spec = importlib.util.spec_from_file_location('parse_watch_export', Path(__file__).with_name('parse-watch-export.py'))
        parser = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(parser)
        rows = {}
        with parser.export_stream(path) as stream:
            context = ET.iterparse(stream, events=('start', 'end'))
            _, root = next(context)
            for event, element in context:
                if event != 'end' or element.tag != 'Record':
                    continue
                row = self._row(element)
                if row:
                    rows[(row['metric'], row['startMs'], row['endMs'], row['device'])] = row
                element.clear()
                root.clear()
        return sorted(rows.values(), key=lambda r: (r['startMs'], r['metric']))

    def _row(self, element):
        attrs = element.attrib
        name = attrs.get('sourceName', '')
        known = self.TYPES.get(attrs.get('type'))
        if not known or self.source.casefold() not in name.casefold() or 'sleepypod' in name.casefold():
            return None
        metric, scale = known
        start, end = [round(datetime.fromisoformat(attrs[k]).timestamp() * 1000) for k in ('startDate', 'endDate')]
        hardware = re.search(r'hardware:([^,\s>]+,\d+|[^,\s>]+)', attrs.get('device', ''))
        algorithm = next((e.get('value') for e in element.iter('MetadataEntry') if e.get('key') == 'HKAlgorithmVersion'), None)
        return measurement(metric, float(attrs['value']) * scale, start, end, hardware.group(1) if hardware else None, algorithm)


class NormalizedFile:
    """Any device's readings as JSON, so a ring or Android watch needs a small converter, not new code here.

    {"device": "Ring 4", "measurements": [{"metric": "hrv_rmssd", "value": 42, "unit": "ms",
    "startMs": 1767333600000, "endMs": 1767333900000, "algorithm": "2"}]}
    """
    def measurements(self, path):
        with closing(Path(path).open()) as file:
            data = json.load(file)
        rows = data.get('measurements') if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ValueError('Expected {"measurements": [...]}')
        result = []
        for row in rows:
            unit = METRICS.get(row.get('metric'), (None,))[0]
            if row.get('unit') != unit:
                raise ValueError(f'{row.get("metric")}: expected unit {unit}')
            result.append(measurement(row['metric'], row.get('value'), row.get('startMs'), row.get('endMs'),
                                      row.get('device', data.get('device')), row.get('algorithm')))
        return sorted((r for r in result if r), key=lambda r: (r['startMs'], r['metric']))
