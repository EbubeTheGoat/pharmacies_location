"""
Run this once before first deployment to generate your VAPID keys.
Copy the output into your .env file.

Usage:
    python generate_vapid_keys.py
"""
import base64
from pywebpush import Vapid
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

v = Vapid()
v.generate_keys()

private_pem = v.private_pem().decode().strip()

raw_pub = v._public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
public_b64 = base64.urlsafe_b64encode(raw_pub).rstrip(b"=").decode()

print("Add these to your .env:\n")
print(f"VAPID_PRIVATE_KEY={private_pem!r}")
print(f"VAPID_PUBLIC_KEY={public_b64}")
print(f'VAPID_EMAIL=mailto:you@example.com')
print()
print("Note: keep VAPID_PRIVATE_KEY secret. VAPID_PUBLIC_KEY is safe to expose.")
