"""Domain profiles: renamed profiles keep working for projects that saved the old id."""
from __future__ import annotations

import pytest

from app.services import domain_profiles


def test_bundled_profile_is_listed():
    assert [p["id"] for p in domain_profiles.list_profiles()] == ["shipping-registers-1900s"]


def test_legacy_id_resolves_to_the_current_profile():
    assert domain_profiles.canonical_id("tabularium-list-1900s") == "shipping-registers-1900s"
    assert domain_profiles.get_profile("tabularium-list-1900s")["id"] == "shipping-registers-1900s"


def test_unknown_id_passes_through_and_get_fails():
    assert domain_profiles.canonical_id("custom-profile") == "custom-profile"
    with pytest.raises(ValueError):
        domain_profiles.get_profile("custom-profile")
