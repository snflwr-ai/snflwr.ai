import os
import sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization


def generate_keypair() -> tuple[bytes, bytes]:
    priv = Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv_pem, pub_pem


def main(out_dir: str = ".") -> None:
    priv_pem, pub_pem = generate_keypair()
    priv_path = os.path.join(out_dir, "signing_key.pem")
    pub_path = os.path.join(out_dir, "license_public_key.pem")
    with open(priv_path, "wb") as f:
        f.write(priv_pem)
    os.chmod(priv_path, 0o600)
    with open(pub_path, "wb") as f:
        f.write(pub_pem)
    print(f"Wrote {priv_path} (0600) and {pub_path}")
    print("Bundle license_public_key.pem into the app at config/license_public_key.pem")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
