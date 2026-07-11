import json
import math
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pywebpush import WebPushException, webpush
from sqlalchemy.orm import Session

from cache import get_user_cache, set_user_cache
from database import get_db
from logger_config import get_logger
from model import PlaceVisited, PushSubscription
from schema import ProximityCheckPayload, PushSubscribeOut, PushSubscribePayload

logger = get_logger("push")
router = APIRouter(tags=["Push Notifications"])

VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY")
VAPID_PUBLIC_KEY  = os.getenv("VAPID_PUBLIC_KEY")
VAPID_EMAIL       = os.getenv("VAPID_EMAIL", "mailto:you@example.com")

# How long (seconds) before the same place can trigger another notification.
# 1800 = 30 minutes. Prevents repeated pings while you're standing still.
COOLDOWN_SECONDS = 1_800

if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
    logger.warning(
        "VAPID_PRIVATE_KEY / VAPID_PUBLIC_KEY not set. "
        "Push notifications will not be sent. "
        "Run `python generate_vapid_keys.py` to create them."
    )


# ── Haversine ─────────────────────────────────────────────────────────────────

def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Returns the great-circle distance in metres between two GPS coordinates.
    Accurate to within ~0.5% for distances up to a few hundred kilometres.
    """
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

def _send_push(subscription: PushSubscription, place: PlaceVisited, distance_m: float) -> bool:
    """
    Sends a Web Push notification to the given subscription.
    Returns True on success, False if the subscription is expired/invalid.
    Raises RuntimeError for config problems (missing VAPID keys).
    """
    if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
        raise RuntimeError("VAPID keys not configured")

    payload = json.dumps({
        "title": "You're near a saved place",
        "body":  f"{distance_m:.0f}m away · {place.latitude:.4f}°, {place.longitude:.4f}°",
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
        # HTTP 410 Gone = the subscription was cancelled by the browser.
        # Any other error = transient network/config problem.
        if e.response is not None and e.response.status_code == 410:
            logger.warning(f"Push subscription {subscription.id} expired (410) — should be deleted")
            return False
        logger.error(f"WebPush failed for subscription {subscription.id}: {e}")
        return False


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/push/vapid-public-key")
def get_vapid_public_key():
    """
    The frontend fetches this once on startup to create the push subscription.
    The public key is safe to expose — it's public by definition.
    """
    if not VAPID_PUBLIC_KEY:
        raise HTTPException(status_code=503, detail="Push notifications not configured")
    return {"public_key": VAPID_PUBLIC_KEY}


@router.post("/push/subscribe", response_model=PushSubscribeOut)
def subscribe(payload: PushSubscribePayload, db: Session = Depends(get_db)):
    """
    Register (or refresh) a browser push subscription.
    Called once after the user grants notification permission.
    Upserting on endpoint means re-subscribing after a permission reset
    doesn't create a duplicate row.
    """
    existing = (
        db.query(PushSubscription)
        .filter(PushSubscription.endpoint == payload.endpoint)
        .first()
    )

    if existing:
        # Update keys in case the browser rotated them
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
    """
    Called by the frontend every ~30 seconds while tracking is active.
    Computes distance from the user's current position to every saved place.
    For each place within `radius` metres that hasn't been alerted in the
    last 30 minutes, fires a Web Push notification.

    Returns the number of places within range and how many notifications
    were sent (useful for debugging on the frontend).
    """
    subscription = db.get(PushSubscription, payload.subscription_id)
    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found. Re-subscribe.")

    places = db.query(PlaceVisited).all()

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
        # Redis key is per (subscription, place) pair — each device tracks
        # its own cooldown independently.
        cooldown_key = f"proximity:{payload.subscription_id}:{place.id}"

        if get_user_cache(cooldown_key):
            continue  # already alerted recently, skip

        try:
            ok = _send_push(subscription, place, dist)
        except RuntimeError as e:
            # VAPID not configured — log once and stop trying
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
            # 410 expired subscription — tell the frontend to re-subscribe
            raise HTTPException(
                status_code=410,
                detail="Push subscription expired. Please re-subscribe.",
            )

    return {
        "places_in_range": len(within_range),
        "notifications_sent": sent,
    }
