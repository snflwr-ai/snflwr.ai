# Encryption Key Recovery

If you cannot read the encrypted SQLite database after a restore, container migration, or laptop replacement, this document is your recovery path. Read it *before* you need it.

The current encryption design (see `storage/encryption.py` and `core/key_management.py`) layers two protections on the database key:

1. The DB encryption key is stored *wrapped* — encrypted with a second key derived from an **operator passphrase** via PBKDF2-HMAC-SHA512 (600,000 iterations). The wrap is broken into salt + ciphertext stored alongside the app, but unwrapping requires the passphrase.
2. The unwrapped key can also be split via **Shamir's Secret Sharing** into N shares with a recovery threshold of K (default 5/3). Shares are designed to be distributed to trusted humans who cannot all be compromised at the same time.

You need exactly one of: the passphrase, or K of N shares. Without either, the encrypted data is unrecoverable. PBKDF2 is one-way; the wrap is AES.

## Inventory before incident

Build this inventory on a clean install and keep it current. The middle of an outage is the wrong time to remember whether you set up Shamir shares.

| Item | Location | Sensitivity |
|---|---|---|
| `DB_ENCRYPTION_KEY` (wrapped) | `.env` on production host | Useless without the passphrase |
| Salt / wrap metadata | `config/encryption.meta.json` (or wherever `KeyManager` writes it) | Useless without the passphrase |
| Operator passphrase | Password manager + offline paper backup in a sealed envelope | **Catastrophic if lost** |
| Shamir shares (if used) | Distributed to K trusted holders, not co-located | Each share alone reveals nothing |
| Recovery test record | `docs/guides/DR_RUNBOOK.md` schedules quarterly drill | — |

## Recovery paths in order of preference

### 1. Restore the passphrase from a password manager

Most common case: the host died, the `.env` is recoverable from a backup, but the operator who set up the passphrase is the same person handling recovery. Pull the passphrase from 1Password / Bitwarden / whatever you use, then:

```python
from core.key_management import KeyManager
km = KeyManager()
key = km.recover_key_from_passphrase("<passphrase>")  # core/key_management.py:548
# `key` is the Fernet-encoded encryption key. Put it back in $DB_ENCRYPTION_KEY.
```

If the passphrase you have doesn't unwrap the key, the wrap metadata (`config/encryption.meta.json`) on disk does not match the `.env` value. Make sure both came from the same backup snapshot. Restoring `.env` from one date and `encryption.meta.json` from another will fail this step silently.

### 2. Reconstruct from Shamir shares

If the passphrase is lost (operator unavailable, password manager wiped, etc.) and you ran `create_key_shares` during setup, you need K shares back.

```python
from core.key_management import KeyManager
km = KeyManager()

shares = [
    "1:<hex-from-share-holder-A>",
    "2:<hex-from-share-holder-B>",
    "3:<hex-from-share-holder-C>",
]
key = km.recover_from_emergency_shares(shares)  # core/key_management.py:732
# `key` is the unwrapped Fernet key. Stash in $DB_ENCRYPTION_KEY.
```

Each share is independently useless. The format is `index:hex`. The order of shares does not matter; their indices do.

### 3. Restore from an older, still-readable replica

If you have an off-host backup taken before the wrap was applied (or before the passphrase was rotated), restoring that backup gets you a DB you can read. You lose every write since the snapshot. This is the path of last resort.

## What does not work

- **Brute forcing the passphrase**: PBKDF2 with 600k iterations is intentionally too slow. A 20-character random passphrase out of base94 alphabet is ~131 bits; you will not get it back without the password manager.
- **Reading the SQLite file without the key**: SQLCipher does not store the encryption key anywhere on disk. There is no recovery oracle. The .db file is literally random-looking bytes.
- **Restoring `.env` from a different machine**: each install generates its own wrap salt. Cross-host transplants need either the passphrase from the source or a fresh re-encryption (which requires reading the source first).

## Setting up Shamir shares during install (do this once)

```python
from core.key_management import KeyManager, create_key_shares
km = KeyManager()

# Generate or load the current encryption key.
key = km.recover_key_from_passphrase("<passphrase>")  # or km.current_key()

# Split into 5 shares, any 3 of which can reconstruct.
shares = create_key_shares(key, total_shares=5, threshold=3)

for i, share in enumerate(shares):
    print(f"Share {i+1}: {share}")
```

Distribute the shares physically — print them, seal them in envelopes, hand them to humans who do not work in the same building. Trusted holders for a K-12 deployment commonly include the principal, IT director, superintendent, school board chair, and an offsite operator (your home safe, your lawyer's vault). One holder = one share. Holders should *not* know who else holds shares.

Set a calendar reminder every 12 months to verify each holder still has theirs and the printout is still legible.

## Recovery drill

Once a quarter, on a non-production environment:

1. Take a backup of the current encrypted DB.
2. Wipe the `.env` and `encryption.meta.json` from the test host.
3. Recover the key via the passphrase path. Confirm decryption.
4. Wipe again. Recover via Shamir shares (use K of N). Confirm decryption.
5. Record the result in `audit_log`-style: `{drill_date, passphrase_path_ok, shamir_path_ok, holders_present}`.

If either path fails, the recovery design is broken. Treat it as a high-severity finding and fix before the next backup overwrites the last known-good state.
