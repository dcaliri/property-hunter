"""Tests for feature engineering used by the valuation model (US2)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from property_hunter.ml.features import feature_names, feature_vector

NUMERIC = ("beds", "baths", "covered_area_m2", "total_area_m2", "age_days")


class _Row(dict):
    def __getitem__(self, key):
        return self.get(key)


def _base_row(**overrides) -> _Row:
    row = _Row(
        beds=2, baths=1, covered_area_m2=55.0, total_area_m2=70.0,
        barrio="Palermo", property_type="departamento", date_posted=None,
        llm_features=None,
    )
    row.update(overrides)
    return row


def test_feature_vector_base_values():
    row = _base_row(
        date_posted=(datetime.now(timezone.utc) - timedelta(days=365)).isoformat())
    vec = feature_vector(row, barrio_values=["Palermo", "Almagro"],
                         type_values=["departamento", "casa"],
                         include_llm_features=False)
    names = feature_names(["Palermo", "Almagro"], ["departamento", "casa"],
                          include_llm_features=False)

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
    row = _base_row(beds=None, baths=None, covered_area_m2=None, total_area_m2=None,
                    barrio=None, property_type=None)
    vec = feature_vector(row, barrio_values=["Palermo"], type_values=["departamento"],
                         include_llm_features=False)
    assert vec[0:5] == [0.0, 0.0, 0.0, 0.0, 0.0]
    assert vec[5] == 0.0
    assert vec[6] == 0.0


def test_feature_names_deterministic():
    names_a = feature_names(["Palermo", "Almagro"], ["casa", "departamento"])
    names_b = feature_names(["Almagro", "Palermo"], ["departamento", "casa"])
    assert names_a == names_b


def test_llm_features_appended_by_default():
    row = _base_row(llm_features=json.dumps({
        "condition": "a_refaccionar", "floor": 3, "expensas": 120000,
        "orientation": "N", "has_parking": True, "has_pool": False,
        "has_gym": True, "has_terrace": False, "has_balcony": True,
        "has_security": True,
    }))
    names_on = feature_names(["Palermo"], ["departamento"], include_llm_features=True)
    names_off = feature_names(["Palermo"], ["departamento"], include_llm_features=False)
    vec_on = feature_vector(row, ["Palermo"], ["departamento"], include_llm_features=True)
    vec_off = feature_vector(row, ["Palermo"], ["departamento"], include_llm_features=False)

    assert len(names_on) > len(names_off)
    assert names_on[:len(names_off)] == names_off
    assert vec_off == vec_on[:len(names_off)]

    assert vec_on[names_on.index("condition::a_refaccionar")] == 1.0
    assert vec_on[names_on.index("condition::nuevo")] == 0.0
    assert vec_on[names_on.index("orientation::N")] == 1.0
    assert vec_on[names_on.index("floor")] == 3.0
    assert vec_on[names_on.index("expensas")] == 120000.0
    assert vec_on[names_on.index("has_parking")] == 1.0
    assert vec_on[names_on.index("has_pool")] == 0.0
    assert vec_on[names_on.index("has_gym")] == 1.0
    assert vec_on[names_on.index("has_security")] == 1.0


def test_llm_features_missing_are_zeros():
    row = _base_row(llm_features=None)
    names = feature_names(["Palermo"], ["departamento"])
    vec = feature_vector(row, ["Palermo"], ["departamento"])

    assert vec[names.index("condition::a_estrenar")] == 0.0
    assert vec[names.index("orientation::N")] == 0.0
    assert vec[names.index("floor")] == 0.0
    assert vec[names.index("has_parking")] == 0.0


def test_llm_features_unparseable_is_zeros():
    row = _base_row(llm_features="not-json")
    names = feature_names(["Palermo"], ["departamento"])
    vec = feature_vector(row, ["Palermo"], ["departamento"])

    assert vec[names.index("has_parking")] == 0.0
    assert vec[names.index("floor")] == 0.0
