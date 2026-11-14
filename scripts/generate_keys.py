# Generates the RSA key pair used to sign (private) and verify (public) JWTs.
# Run once from the repository root, before `docker compose up`:
#   python scripts/generate_keys.py
# Needs the `cryptography` package on your machine (pip install cryptography).
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

PRIVATE_KEY_PATH = "keys/private/private.pem"
PUBLIC_KEY_PATH = "keys/public/public.pem"

# Step 1: never overwrite existing keys (that would invalidate every issued token)
if os.path.exists(PRIVATE_KEY_PATH) or os.path.exists(PUBLIC_KEY_PATH):
    print("Keys already exist, nothing changed. Delete the keys/ folder to create new ones.")
    raise SystemExit(0)

# Step 2: generate a 2048-bit RSA key pair
private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

# Step 3: turn both keys into PEM text
private_pem = private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)
public_pem = private_key.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
)

# Step 4: write them into two separate folders (only Identity mounts the private one)
os.makedirs("keys/private", exist_ok=True)
os.makedirs("keys/public", exist_ok=True)
with open(PRIVATE_KEY_PATH, "wb") as private_file:
    private_file.write(private_pem)
with open(PUBLIC_KEY_PATH, "wb") as public_file:
    public_file.write(public_pem)

# Step 5: the private key is readable by its owner only
os.chmod(PRIVATE_KEY_PATH, 0o600)

print(f"Created {PRIVATE_KEY_PATH} and {PUBLIC_KEY_PATH}")
