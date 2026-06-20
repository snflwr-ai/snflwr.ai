# Bundled license public key

`license_public_key.pem` is the **public** half of the License Server's Ed25519
signing key. The self-hosted app uses it to verify license tokens **offline**
(see `core/licensing.py`). It is **not** a secret — it ships with the app.

> ⚠️ The file currently checked in is a **DEV PLACEHOLDER** generated locally.
> It does **not** correspond to any real signing key, so real tokens will not
> verify against it. This is harmless while `LICENSE_ENFORCED=false` (the
> default — see `config.py`).

## Phase 0 (before enabling enforcement)

1. Generate the real keypair on the License Server:
   `cd license-server && python -m app.keygen /secure/dir`
2. Copy the **public** key here, replacing the placeholder:
   `cp /secure/dir/license_public_key.pem config/license_public_key.pem`
3. Keep `signing_key.pem` (the private key) **only** on the License Server /
   in its secret store. Never commit it.
4. Set `LICENSE_ENFORCED=true` once billing + legal are live.
