"""Pydantic v2 domain schemas mirroring contracts/*.json.

These are the explicit data contracts between pipeline stages
(constitution V: modular pipeline, explicit contracts).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Operation = Literal["sale", "rent"]
PropertyType = Literal["departamento", "casa", "ph", "lote", "oficina", "local", "otro"]
SignalType = Literal["undervaluation", "yield", "price_drop"]


class ListingRecord(BaseModel):
    """Normalized listing record emitted by the normalize stage (listing-v1)."""

    source: Literal["inmoup"] = "inmoup"
    source_listing_id: int
    source_url: str
    operation: Operation
    property_type: PropertyType | None = None
    street_address: str | None = None
    barrio: str
    region: str
    lat: float | None = None
    lng: float | None = None
    beds: int | None = Field(default=None, ge=0)
    baths: int | None = Field(default=None, ge=0)
    covered_area_m2: float | None = Field(default=None, ge=0)
    total_area_m2: float | None = Field(default=None, ge=0)
    agency_name: str | None = None
    description: str | None = None
    llm_amenity_tags: list[str] | None = None
    date_posted: str | None = None
    price_cents: int = Field(ge=0)
    currency: Literal["USD", "ARS"] = "USD"
    raw_price_text: str | None = None
    observed_at: str


class PriceObservation(BaseModel):
    """Per-run observation snapshot of a listing's asking price."""

    run_id: int
    listing_id: int
    price_cents: int
    currency: str = "USD"
    observed_at: str
    page_id: int | None = None
    is_active: bool = True


class Signal(BaseModel):
    """A single rule evaluation result for a property (detection-v1 signal)."""

    type: SignalType
    threshold: float
    observed: float | None = None
    expected: float | None = None
    satisfied: bool = False
    model_version_id: int | None = None
    is_fallback: bool | None = None


class DetectionRecord(BaseModel):
    """Opportunity detection record emitted by the detect stage (detection-v1)."""

    listing_id: int
    run_id: int
    baseline_id: int | None = None
    prediction_id: int | None = None
    signals: list[Signal]
    score: float = Field(ge=0, le=1)
    status: Literal["active", "superseded", "resolved"] = "active"
    first_seen_at: str
    last_seen_at: str
    created_at: str


class NotificationRecord(BaseModel):
    """Notification delivery record emitted by the notify stage (notification-v1)."""

    detection_id: int
    run_id: int
    channel: Literal["email"] = "email"
    recipient: str
    status: Literal["pending", "sent", "failed"] = "pending"
    attempt_count: int = 0
    last_error: str | None = None
    sent_at: str | None = None
    created_at: str
    digest_narrative: str | None = None
    llm_enriched: bool = False


class BaselineRecord(BaseModel):
    """Immutable per-window zone baseline statistics."""

    zone_id: int
    operation: Operation
    property_type: PropertyType | None = None
    window_start: str
    window_end: str
    observation_count: int = 0
    is_sufficient: bool = False
    median_price_cents: int | None = None
    median_rent_cents: int | None = None
    median_price_per_m2_cents: int | None = None
    computed_at: str


class ModelVersionRecord(BaseModel):
    """Persisted trained valuation model metadata."""

    run_id: int
    trained_at: str
    training_window_start: str
    training_window_end: str
    training_count: int
    r2_score: float | None = None
    mae_cents: int | None = None
    blob: bytes
    is_current: bool = True
    notes: str | None = None


class PredictionRecord(BaseModel):
    """Value estimate for a listing (model-backed or fallback)."""

    listing_id: int
    model_version_id: int | None = None
    run_id: int
    predicted_price_cents: int
    is_fallback: bool = True
    feature_importances: dict[str, float] | None = None
    predicted_at: str
