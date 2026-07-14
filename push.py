import json
import math
import os

from fastapi import APIRouter, Depends, HTTPException
from pywebpush import WebPushException, webpush
from sqlalchemy.orm import Session

from cache import get_user_cache, set_user_cache
from database import get_db
from logger_config import get_logger
from model import PharmacyLead, PushSubscription
from schema import ProximityCheckPayload, PushSubscribeOut, PushSubscribePayload

logger = get_logger("push")
router = APIRouter(tags=["Push Notifications"])

VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY")
VAPID_PUBLIC_KEY  = os.getenv("VAPID_PUBLIC_KEY")
VAPID_EMAIL       = os.getenv("VAPID_EMAIL", "mailto:you@example.com")

COOLDOWN_SECONDS = 1_800

if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
    logger.warning(
        "VAPID_PRIVATE_KEY / VAPID_PUBLIC_KEY not set. "
        "Push notifications will not be sent. "
        "Run `python generate_vapid_keys.py` to create them."
    )

# ── Haversine ─────────────────────────────────────────────────────────────────

def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000  # Earth radius in metres
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi    = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

# ── Push sender ───────────────────────────────────────────────────────────────

def _send_push(subscription: PushSubscription, place: PharmacyLead, distance_m: float) -> bool:
    if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
        raise RuntimeError("VAPID keys not configured")

    payload = json.dumps({
        "title": "You're near a pharmacy!",
        "body":  f"{distance_m:.0f}m away · {place.name}",
        "placeId": place.id,
    })

    try:
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {
                    "p256dh": subscription.p256dh,
                    "auth":   subscription.auth,
                },
            },
            data=payload,
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_EMAIL},
        )
        return True

    except WebPushException as e:
        if e.response is not None and e.response.status_code == 410:
            logger.warning(f"Push subscription {subscription.id} expired (410) — should be deleted")
            return False
        logger.error(f"WebPush failed for subscription {subscription.id}: {e}")
        return False

# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/push/vapid-public-key")
def get_vapid_public_key():
    if not VAPID_PUBLIC_KEY:
        raise HTTPException(status_code=503, detail="Push notifications not configured")
    return {"public_key": VAPID_PUBLIC_KEY}

@router.post("/push/subscribe", response_model=PushSubscribeOut)
def subscribe(payload: PushSubscribePayload, db: Session = Depends(get_db)):
    existing = (
        db.query(PushSubscription)
        .filter(PushSubscription.endpoint == payload.endpoint)
        .first()
    )

    if existing:
        existing.p256dh = payload.p256dh
        existing.auth   = payload.auth
        db.commit()
        db.refresh(existing)
        return {"subscription_id": existing.id}

    sub = PushSubscription(
        endpoint=payload.endpoint,
        p256dh=payload.p256dh,
        auth=payload.auth,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    logger.info(f"New push subscription registered: id={sub.id}")
    return {"subscription_id": sub.id}

@router.post("/push/check-proximity")
def check_proximity(payload: ProximityCheckPayload, db: Session = Depends(get_db)):
    subscription = db.get(PushSubscription, payload.subscription_id)
    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found. Re-subscribe.")

    # Modified: We check proximity strictly against established Pharmacy Leads 
    places = db.query(PharmacyLead).all()

    within_range = []
    for place in places:
        dist = haversine_meters(
            payload.latitude, payload.longitude,
            place.latitude,   place.longitude,
        )
        if dist <= payload.radius:
            within_range.append((place, dist))

    sent = 0
    for place, dist in within_range:
        cooldown_key = f"proximity:{payload.subscription_id}:{place.id}"
        if get_user_cache(cooldown_key):
            continue 

        try:
            ok = _send_push(subscription, place, dist)
        except RuntimeError as e:
            logger.error(f"Push not sent: {e}")
            break

        if ok:
            sent += 1
            set_user_cache(cooldown_key, {"alerted": True}, ttl=COOLDOWN_SECONDS)
            logger.info(
                f"Proximity alert sent: subscription={payload.subscription_id} "
                f"place={place.id} distance={dist:.0f}m"
            )
        else:
            raise HTTPException(
                status_code=410,
                detail="Push subscription expired. Please re-subscribe.",
            )

    return {
        "places_in_range": len(within_range),
        "notifications_sent": sent,
    }
