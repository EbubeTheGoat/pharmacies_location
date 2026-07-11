import os
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from cache import get_user_cache, set_user_cache
from database import get_db
from logger_config import get_logger
from model import PlaceVisited
from schema import LocationCreate, LocationOut

logger = get_logger("locations")
router = APIRouter(tags=["GPS Tracker"])

# Optional shared-secret protection.
# Set API_SECRET in your .env to require an X-API-Key header on these routes.
# Leave it unset to keep the routes open (fine for local dev).
API_SECRET = os.getenv("API_SECRET")
if not API_SECRET:
    logger.warning("API_SECRET not set — /locations endpoints are unauthenticated")


def _require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    if API_SECRET and x_api_key != API_SECRET:
        raise HTTPException(status_code=401, detail="Missing or invalid API key")


def _to_out(place: PlaceVisited) -> LocationOut:
    # Explicit mapping: column is accuracy_meters, API field is accuracy.
    # Relying on automatic attribute matching silently returns null when
    # the names don't match — caught and fixed from the previous version.
    return LocationOut(
        id=place.id,
        latitude=place.latitude,
        longitude=place.longitude,
        accuracy=place.accuracy_meters,
        visited_at=place.visited_at,
    )


@router.post("/locations", response_model=LocationOut, dependencies=[Depends(_require_api_key)])
def log_location(payload: LocationCreate, db: Session = Depends(get_db)):
    try:
        place = PlaceVisited(
            latitude=payload.latitude,
            longitude=payload.longitude,
            accuracy_meters=payload.accuracy,
        )
        db.add(place)
        db.commit()
        db.refresh(place)
        logger.info(f"Logged place id={place.id} ({place.latitude}, {place.longitude})")
        return _to_out(place)
    except Exception as e:
        db.rollback()
        logger.error(f"Error saving location: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.get("/locations", response_model=List[LocationOut], dependencies=[Depends(_require_api_key)])
def list_locations(db: Session = Depends(get_db)):
    try:
        places = (
            db.query(PlaceVisited)
            .order_by(PlaceVisited.visited_at.desc())
            .all()
        )
        return [_to_out(p) for p in places]
    except Exception as e:
        logger.error(f"Error listing locations: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
