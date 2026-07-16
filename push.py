import json
import math
import os
import requests

from fastapi import APIRouter, Depends, HTTPException, Request
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
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

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

# ── Push sender & Telegram Logic ──────────────────────────────────────────────
def get_chat_id(update: dict) -> int | None:
    """
    Extracts the Telegram chat ID from an incoming webhook update.
    """
    try:
        return update["message"]["chat"]["id"]
    except KeyError:
        return None

def save_chat_id(db: Session, user: PharmacyLead, update: dict):
    chat_id = get_chat_id(update)
    if chat_id is None:
        raise ValueError("Could not find chat ID")

    user.phone_number = str(chat_id)
    db.commit()

def send_telegram(chat_id: str, message: str) -> bool:
    """Delivers the update via Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("Telegram Bot Token not configured.")
        return False
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload1 = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload1, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Telegram failed for {chat_id}: {e}")
        return False

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

@router.post("/telegram/webhook")
async def telegram_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Receives incoming webhook updates from Telegram.
    Expects users to send a message like `/start <lead_id>` to link their chat to a lead.
    """
    update = await request.json()
    chat_id = get_chat_id(update)
    
    if not chat_id:
        return {"status": "ignored"}
        
    try:
        text = update["message"]["text"]
        # Link chat ID if user sends /start {lead_id}
        if text.startswith("/start"):
            parts = text.split()
            if len(parts) > 1:
                lead_id = int(parts[1])
                lead = db.get(PharmacyLead, lead_id)
                if lead:
                    save_chat_id(db, lead, update)
                    send_telegram(str(chat_id), f"Successfully linked alerts to {lead.name}.")
                else:
                    send_telegram(str(chat_id), "Pharmacy not found.")
    except (KeyError, ValueError, IndexError) as e:
        logger.warning(f"Failed to parse Telegram message: {e}")
        pass

    return {"status": "ok"}

@router.post("/push/check-proximity")
def check_proximity(payload: ProximityCheckPayload, db: Session = Depends(get_db)):
    subscription = db.get(PushSubscription, payload.subscription_id)
    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found. Re-subscribe.")

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
    expired = False
    
    for place, dist in within_range:
        cooldown_key = f"proximity:{payload.subscription_id}:{place.id}"
        if get_user_cache(cooldown_key):
            continue

        # Handle Telegram Notification
        if place.phone_number:
            try:
                logger.info("Sending to telegram")
                message = f"You are near a pharmacy. {dist:.0f}m away · {place.name}"
                send_telegram(place.phone_number, message)
                logger.info("Message sent to telegram")
            except Exception as e:
                logger.error(f"Telegram not sent: {e}") 

        # Handle Browser Push Notification
        try:
            ok = _send_push(subscription, place, dist)
            if ok:
                sent += 1
                set_user_cache(cooldown_key, {"alerted": True}, ttl=COOLDOWN_SECONDS)
                logger.info(
                    f"Proximity alert sent: subscription={payload.subscription_id} "
                    f"place={place.id} distance={dist:.0f}m"
                )
            else:
                expired = True
        except RuntimeError as e:
            # Replaced break with continue so remaining pharmacies are still checked
            logger.error(f"Push not sent for {place.id}: {e}")
            continue

    # Defer the 410 response until all loops have run
    if expired:
        raise HTTPException(
            status_code=410,
            detail="Push subscription expired. Please re-subscribe.",
        )

    return {
        "places_in_range": len(within_range),
        "notifications_sent": sent,
    }
