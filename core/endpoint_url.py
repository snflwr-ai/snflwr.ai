"""One place that decides whether a URL is a service endpoint we may call.

WHY THIS EXISTS
---------------
Three call sites fetch from a URL that comes from configuration: the thin-client
manifest, the install-time context probe, and the remote inference driver. All
three used `urllib`/`httpx` against a value an operator typed, and each grew (or
needed) its own scheme check. Bandit flags exactly this as B310: `urlopen`
follows `file://` as happily as `http://`, so a mistyped or hostile
`MANAGEMENT_SERVER_URL` / `OLLAMA_BASE_URL` / `INFERENCE_REMOTE_URL` turns a
fetch into a local-file read.

WHY `thin_client` STILL HAS ITS OWN COPY
----------------------------------------
`core/thin_client.py` is deliberately free of the config -> storage ->
cryptography import chain; its tests load it by file path to keep it that way.
Importing this module would defeat that, because `core/__init__.py` pulls
`AuthenticationManager` and friends before any submodule is reached. So
thin_client keeps its private `_validate_url`, and that duplication is
isolation, not sloppiness. If `core/__init__.py` ever stops importing the
managers eagerly, fold thin_client into this module and delete the copy.

This module still imports nothing but the standard library, so it stays cheap
for the installer, which reaches it through `core.context_probe`.

THE LOOPBACK RULE
-----------------
Plain HTTP is allowed only to loopback. A remote engine carries a bearer token
and, for the tutor, a child's text; sending either over cleartext to another host
is not a trade-off worth offering as a config option. Loopback stays HTTP
because that is how the containers on one box already talk to each other.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

ALLOWED_SCHEMES = frozenset(("http", "https"))

# Hostnames that never leave the machine. Anything else is "another host" and
# has to be TLS when the caller asks for transport security.
_LOOPBACK_NAMES = frozenset(("localhost", "localhost.localdomain"))


def is_loopback(host: str) -> bool:
    """True when this host cannot be reached from another machine."""
    if not host:
        return False
    name = host.strip("[]").lower()
    if name in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def validate_endpoint(url: str, *, require_tls_offbox: bool = False) -> str:
    """Return `url` if we may call it, else raise ValueError explaining why.

    `require_tls_offbox` adds the rule that a non-loopback host must be https.
    Callers that carry a credential or student text should set it; a fetch of a
    public manifest on a LAN need not.
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise ValueError(
            f"URL scheme {scheme or '(none)'!r} not allowed; use http or https"
        )
    if not parsed.hostname:
        raise ValueError("URL has no host")
    if require_tls_offbox and scheme == "http" and not is_loopback(parsed.hostname):
        raise ValueError(
            f"refusing cleartext http to {parsed.hostname!r}: "
            "a non-loopback endpoint must use https"
        )
    return url
