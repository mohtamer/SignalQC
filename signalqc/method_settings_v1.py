"""User-selectable scientific settings. Omitted keys retain historical defaults."""
from __future__ import annotations

import math

ACCEPTREJECT_METHODS = {
    '1': 'STA/LTA', '2': 'Bellagamba NN', '3': 'Kishida MV',
    '4': 'Kishida GR', '5': 'Kishida BF', '6': 'Kishida BT',
    '7': 'Kishida SV', '8': 'NCM NN',
}
CORNER_METHODS = {
    'snr_kishida': 'SNR–Kishida', 'empirical': 'Empirical',
    'ancheta': 'Displacement–Ancheta', 'guo': 'Guo (Python)',
    'hk': 'Hussein/Kishida',
}
DEFAULT_ACCEPTREJECT_METHODS = list(ACCEPTREJECT_METHODS)
DEFAULT_CORNER_METHODS = list(CORNER_METHODS)
DEFAULT_ACCEPTREJECT_REQUIRED_VOTES = 5
DEFAULT_FILTERING_HP_FALLBACK_HZ = 0.1
DEFAULT_FILTERING_LP_MIN_HZ = 8.0


def method_defaults():
    return dict(acceptreject_methods=DEFAULT_ACCEPTREJECT_METHODS.copy(),
                acceptreject_required_votes=DEFAULT_ACCEPTREJECT_REQUIRED_VOTES,
                filtering_methods=DEFAULT_CORNER_METHODS.copy(),
                filtering_hp_fallback_hz=DEFAULT_FILTERING_HP_FALLBACK_HZ,
                filtering_lp_min_hz=DEFAULT_FILTERING_LP_MIN_HZ)


def _selected(config, key, choices, label):
    selected = config.get(key, list(choices))
    if (not isinstance(selected, (list, tuple)) or not selected
            or any(not isinstance(value, str) or value not in choices for value in selected)
            or len(set(selected)) != len(selected)):
        raise ValueError(f'Select at least one {label} method, without unknown or duplicate methods.')
    # User display order must never change classifier evaluation order.
    return [value for value in choices if value in selected]


def stage_method_settings(config, stage):
    if stage == 'acceptreject':
        selected = _selected(config, 'acceptreject_methods', ACCEPTREJECT_METHODS, 'accept/reject')
        votes = config.get('acceptreject_required_votes', DEFAULT_ACCEPTREJECT_REQUIRED_VOTES)
        if isinstance(votes, bool) or not isinstance(votes, int) or not 1 <= votes <= len(selected):
            raise ValueError(f'Accept/reject requires between 1 and {len(selected)} accepting votes for the selected methods.')
        return dict(acceptreject_methods=selected, acceptreject_required_votes=votes)
    if stage == 'filtering':
        selected = _selected(config, 'filtering_methods', CORNER_METHODS, 'corner-frequency')
        numbers = {}
        for key, default, label in [
            ('filtering_hp_fallback_hz', DEFAULT_FILTERING_HP_FALLBACK_HZ, 'High-pass fallback'),
            ('filtering_lp_min_hz', DEFAULT_FILTERING_LP_MIN_HZ, 'Low-pass minimum/fallback'),
        ]:
            value = config.get(key, default)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value <= 0):
                raise ValueError(f'{label} must be a finite frequency greater than zero.')
            numbers[key] = float(value)
        if numbers['filtering_hp_fallback_hz'] >= numbers['filtering_lp_min_hz']:
            raise ValueError('High-pass fallback must be lower than the low-pass minimum/fallback.')
        return dict(filtering_methods=selected, **numbers)
    return {}


def method_setting_errors(config):
    errors = []
    for stage in ('acceptreject', 'filtering'):
        if stage in config.get('stages', []):
            try:
                stage_method_settings(config, stage)
            except ValueError as exc:
                errors.append(str(exc))
    return errors
