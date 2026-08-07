"""Tests for feature engineering used by the valuation model (US2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from property_hunter.ml.features import feature_names, feature_vector

NUMERIC = ("beds", "baths", "covered_area_m2", "total_area_m2", "age_days")


class _Row(dict):
    def __getitem__(self, key):
        return self.get(key)


def test_feature_vector_values():
    row = _Row(beds=2, baths=1, covered_area_m2=55.0, total_area_m2=70.0,
               barrio="Palermo", property_type="departamento",
               date_posted=(datetime.now(timezone.utc) - timedelta(days=365)).isoformat())
    vec = feature_vector(row, barrio_values=["Palermo", "Almagro"],
                         type_values=["departamento", "casa"])
    names = feature_names(["Palermo", "Almagro"], ["departamento", "casa"])

    assert names[:5] == list(NUMERIC)
    assert names[5:] == ["barrio::Almagro", "barrio::Palermo", "type::casa", "type::departamento"]

    assert vec[0] == 2.0
    assert vec[1] == 1.0
    assert vec[2] == 55.0
    assert vec[3] == 70.0
    assert abs(vec[4] - 365.0) < 3.0
    assert vec[names.index("barrio::Palermo")] == 1.0
    assert vec[names.index("barrio::Almagro")] == 0.0
    assert vec[names.index("type::departamento")] == 1.0


def test_feature_vector_nulls():
    row = _Row(beds=None, baths=None, covered_area_m2=None, total_area_m2=None,
               barrio=None, property_type=None, date_posted=None)
    vec = feature_vector(row, barrio_values=["Palermo"], type_values=["departamento"])
    assert vec[0:5] == [0.0, 0.0, 0.0, 0.0, 0.0]
    assert vec[5] == 0.0
    assert vec[6] == 0.0


def test_feature_names_deterministic():
    names_a = feature_names(["Palermo", "Almagro"], ["casa", "departamento"])
    names_b = feature_names(["Almagro", "Palermo"], ["departamento", "casa"])
    assert names_a == names_b
