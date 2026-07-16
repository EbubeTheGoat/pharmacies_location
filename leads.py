from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from database import get_db
from logger_config import get_logger
from model import PharmacyLead, PharmacyVisit
from schema import PharmacyLeadCreate, PharmacyLeadOut

logger = get_logger("leads")
router = APIRouter(tags=["Pharmacy Leads"])

@router.post("/register_leads", response_model=PharmacyLeadOut)
def register_lead(lead: PharmacyLeadCreate, db: Session = Depends(get_db)):
    """
    Register a pharmacy lead by name and coordinates.
    If it exists, logs an additional PharmacyVisit and returns the existing lead.
    If it doesn't, creates the PharmacyLead and logs the initial PharmacyVisit.
    """
    lat = round(lead.latitude, 5)
    lon = round(lead.longitude, 5)
    epsilon = 0.00001  # Tolerance for float comparison (~1 meter)

    try:
        # Use epsilon to avoid float equality mismatch errors
        existing = (
            db.query(PharmacyLead)
            .filter(
                PharmacyLead.latitude >= lat - epsilon,
                PharmacyLead.latitude <= lat + epsilon,
                PharmacyLead.longitude >= lon - epsilon,
                PharmacyLead.longitude <= lon + epsilon
            )
            .first()
        )

        if existing:
            # Register a visit for the existing pharmacy lead
            visit = PharmacyVisit(lead_id=existing.id)
            db.add(visit)
            db.commit()
            db.refresh(existing)
            return existing

        # Create new pharmacy lead
        db_lead = PharmacyLead(
            name=lead.name,
            latitude=lat,
            longitude=lon,
        )
        db.add(db_lead)
        db.commit()
        db.refresh(db_lead)
        
        # Log the first visit
        visit = PharmacyVisit(lead_id=db_lead.id)
        db.add(visit)
        db.commit()

        return db_lead

    except Exception as e:
        db.rollback()
        logger.error(f"Error registering lead: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.get("/leads", response_model=List[PharmacyLeadOut])
def get_leads(db: Session = Depends(get_db)):
    """
    Retrieves all registered pharmacy leads.
    """
    try:
        leads = (
            db.query(PharmacyLead)
            .order_by(PharmacyLead.created_at.desc())
            .all()
        )
        return leads
    except Exception as e:
        logger.error(f"Error listing leads: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")