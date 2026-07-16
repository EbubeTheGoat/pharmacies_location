import sqlalchemy
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from database import Base


class PlaceVisited(Base):
    """
    GPS coordinates you've been to, logged from your phone.
    This is the core model for the places-visited map feature.
    """
    __tablename__ = "places_visited"

    id = Column(Integer, primary_key=True, index=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    accuracy_meters = Column(Float, nullable=True)
    visited_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )


class PharmacyLead(Base):
    """
    Pharmacy lead registration — the original /register_leads feature.
    latitude/longitude replace the old opaque `location: String` column
    so coordinates are stored properly rather than as a freeform string.
    """
    __tablename__ = "pharmacy_leads"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    # Changed nullable to True to prevent IntegrityError on map log creation
    phone_number = Column(String, nullable=False) 
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    visits = relationship("PharmacyVisit", back_populates="lead", cascade="all, delete-orphan")


class PharmacyVisit(Base):
    """
    Each time a pharmacy lead is visited/updated. Was called `Time` before,
    renamed because `Time` described nothing about what the row represented.
    The missing ForeignKey back to PharmacyLead is now wired up.
    """
    __tablename__ = "pharmacy_visits"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("pharmacy_leads.id"), nullable=False, index=True)
    date_visited = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    lead = relationship("PharmacyLead", back_populates="visits")


class PushSubscription(Base):
    """
    One row per browser/device that has opted in to push notifications.
    endpoint is unique per browser — if the same browser re-subscribes
    (e.g. after a permission reset) we upsert rather than duplicate.
    """
    __tablename__ = "push_subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    endpoint = Column(String, unique=True, nullable=False)
    p256dh = Column(String, nullable=False)   # browser's public key
    auth = Column(String, nullable=False)      # shared auth secret
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
