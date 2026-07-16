from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator


# ── GPS tracker ──────────────────────────────────────────────────────────────

class LocationCreate(BaseModel):
    latitude: float
    longitude: float
    accuracy: Optional[float] = None  # metres, from navigator.geolocation

    @field_validator("latitude")
    @classmethod
    def latitude_in_range(cls, v: float) -> float:
        if not -90 <= v <= 90:
            raise ValueError("latitude must be between -90 and 90")
        return v

    @field_validator("longitude")
    @classmethod
    def longitude_in_range(cls, v: float) -> float:
        if not -180 <= v <= 180:
            raise ValueError("longitude must be between -180 and 180")
        return v


class LocationOut(BaseModel):
    id: int
    latitude: float
    longitude: float
    accuracy: Optional[float] = None
    visited_at: datetime

    model_config = {"from_attributes": True}


# ── Pharmacy leads ────────────────────────────────────────────────────────────

class PharmacyLeadCreate(BaseModel):
    """
    Replaces the original UserBase. `location: int` made no sense — a
    single integer can't hold a coordinate pair. Now uses proper lat/lon.
    `name` must be at least 2 characters (the normalize_name logic from
    main.py is now enforced here at schema level instead of dead-code).
    """
    name: str
    latitude: float
    longitude: float

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) < 2:
            raise ValueError("Please enter a full name (at least 2 characters).")
        return cleaned

    @field_validator("latitude")
    @classmethod
    def latitude_in_range(cls, v: float) -> float:
        if not -90 <= v <= 90:
            raise ValueError("latitude must be between -90 and 90")
        return v

    @field_validator("longitude")
    @classmethod
    def longitude_in_range(cls, v: float) -> float:
        if not -180 <= v <= 180:
            raise ValueError("longitude must be between -180 and 180")
        return v


class PharmacyLeadOut(BaseModel):
    id: int
    name: str
    latitude: float
    longitude: float
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Push notifications ────────────────────────────────────────────────────────

class PushSubscribePayload(BaseModel):
    """Sent by the browser after the user grants notification permission."""
    endpoint: str
    p256dh: str
    auth: str


class PushSubscribeOut(BaseModel):
    subscription_id: int


class ProximityCheckPayload(BaseModel):
    """
    Sent by the browser every ~30 seconds while tracking is active.
    The server checks for saved places within `radius` metres and fires
    a push notification for any not recently alerted.
    """
    latitude: float
    longitude: float
    subscription_id: int
    radius: float = 500.0

    @field_validator("latitude")
    @classmethod
    def latitude_in_range(cls, v: float) -> float:
        if not -90 <= v <= 90:
            raise ValueError("latitude must be between -90 and 90")
        return v

    @field_validator("longitude")
    @classmethod
    def longitude_in_range(cls, v: float) -> float:
        if not -180 <= v <= 180:
            raise ValueError("longitude must be between -180 and 180")
        return v

    @field_validator("radius")
    @classmethod
    def radius_in_range(cls, v: float) -> float:
        if not 50 <= v <= 10_000:
            raise ValueError("radius must be between 50 and 10,000 metres")
        return v
