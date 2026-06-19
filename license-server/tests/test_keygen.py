from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key
from app.keygen import generate_keypair


def test_generate_keypair_roundtrip():
    priv_pem, pub_pem = generate_keypair()
    priv = load_pem_private_key(priv_pem, password=None)
    pub = load_pem_public_key(pub_pem)
    sig = priv.sign(b"hello")
    pub.verify(sig, b"hello")  # raises if mismatch
