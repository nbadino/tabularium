"""Whether a project's model tables pass through the structure guard, and with which column types.

The rules live in `domain/table_guard.py`; the column types in the domain profile
(`table_guard.columns`). This module only decides, per project:

* `table_guard` in the project settings: `true`/`false` set by the user;
* absent: the profile's `table_guard.enabled` (documented per profile), else off.

The project's profile is its study protocol's `domain_profile`, or the protocol's
default when no protocol was saved (`domain_profiles.DEFAULT_PROFILE_ID`).
"""
from __future__ import annotations

from dataclasses import dataclass

from ..domain import table_guard as rules
from . import domain_profiles

_KEY = "table_guard"


@dataclass(frozen=True)
class GuardSettings:
    enabled: bool
    # "project" when the user set it, "profile" when it follows the profile.
    source: str
    profile_id: str
    profile_default: bool
    columns: tuple[rules.ColumnType, ...]


def profile_id_of(settings: dict | None) -> str:
    protocol = (settings or {}).get("study_protocol") or {}
    return domain_profiles.canonical_id(str(protocol.get("domain_profile") or domain_profiles.DEFAULT_PROFILE_ID))


def resolve(settings: dict | None) -> GuardSettings:
    profile_id = profile_id_of(settings)
    try:
        spec = domain_profiles.get_profile(profile_id).get("table_guard") or {}
    except ValueError:
        spec = {}
    columns = rules.parse_columns(spec.get("columns"))
    profile_default = bool(spec.get("enabled", False))
    explicit = (settings or {}).get(_KEY)
    if isinstance(explicit, bool):
        return GuardSettings(explicit, "project", profile_id, profile_default, columns)
    return GuardSettings(profile_default, "profile", profile_id, profile_default, columns)


def set_enabled(settings: dict, enabled: bool | None) -> None:
    """`None` returns the project to its profile's default."""
    if enabled is None:
        settings.pop(_KEY, None)
    else:
        settings[_KEY] = bool(enabled)


def apply(grid: dict | None, guard: GuardSettings) -> dict | None:
    """The model's grid after the guard, or unchanged when the guard is off."""
    if not guard.enabled or not grid:
        return grid
    return rules.guard_grid(grid, guard.columns)
