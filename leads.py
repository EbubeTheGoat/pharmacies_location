from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from cache import get_user_cache, set_user_cache
from database import get_db
from logger_config import get_logger
from model import PharmacyLead
from schema import PharmacyLeadCreate, PharmacyLeadOut

logger = get_logger("leads")
router = APIRouter(tags=["Pharmacy Leads"])


@router.post("/register_leads", response_model=PharmacyLeadOut)
def register_lead(lead: PharmacyLeadCreate, db: Session = Depends(get_db)):
    """
    Register a pharmacy lead by name and coordinates.
    Returns the existing record if a lead with the same coordinates already exists.

    Bugs fixed from original:
      1. `UserBase.location` (class attribute access) → `lead.latitude/longitude` (instance)
      2. `UserBase.name` (class attribute access)     → `lead.name` (instance)
      3. `PharmacyLead(name, location)` (positional)  → keyword args, required by SQLAlchemy
    """
    # Round to 5 decimal places (~1m precision) so two readings a metre apart
    # at the same pharmacy don't create duplicate records.
    lat = round(lead.latitude, 5)
    lon = round(lead.longitude, 5)
    cache_key = f"{lat}:{lon}"

    # Cache check first — avoid a DB hit for a recently seen location.
    cached = get_user_cache(cache_key)
    if cached:
        return cached

    try:
        existing = (
            db.query(PharmacyLead)
            .filter(PharmacyLead.latitude == lat, PharmacyLead.longitude == lon)
            .first()
        )

        if existing:
            result = PharmacyLeadOut.model_validate(existing)
            set_user_cache(cache_key, result.model_dump(mode="json"))
            return result

        db_lead = PharmacyLead(
            name=lead.name,
            latitude=lat,
            longitude=lon,
        )
        db.add(db_lead)
        db.commit()
        db.refresh(db_lead)

        result = PharmacyLeadOut.model_validate(db_lead)
        set_user_cache(cache_key, result.model_dump(mode="json"))
        return result

    except Exception as e:
        db.rollback()
        logger.error(f"Error registering lead: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
