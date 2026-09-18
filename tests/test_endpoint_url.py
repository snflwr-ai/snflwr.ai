"""The one place that decides whether a configured URL may be fetched.

Bandit B310 is the reason this exists: `urlopen` follows `file://` as happily as
`http://`, and three call sites fetch a URL an operator typed.
"""

import pytest

from core.endpoint_url import is_loopback, validate_endpoint


class TestSchemes:
    @pytest.mark.parametrize("url", ["http://ollama:11434", "https://tutor.example/x"])
    def test_http_and_https_pass(self, url):
        assert validate_endpoint(url) == url

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://host/x",
            "gopher://host",
            "/no/scheme",
            "tutor.example",
        ],
    )
    def test_everything_else_is_refused(self, url):
        with pytest.raises(ValueError):
            validate_endpoint(url)

    def test_a_url_with_no_host_is_refused(self):
        with pytest.raises(ValueError, match="no host"):
            validate_endpoint("http://")


class TestLoopback:
    @pytest.mark.parametrize(
        "host", ["localhost", "127.0.0.1", "127.9.9.9", "::1", "[::1]", "LOCALHOST"]
    )
    def test_loopback_hosts(self, host):
        assert is_loopback(host)

    @pytest.mark.parametrize("host", ["ollama", "10.0.0.5", "tutor.example", "", "0.0.0.0"])
    def test_non_loopback_hosts(self, host):
        assert not is_loopback(host)


class TestTlsOffBox:
    """A credential and a child's text are about to cross this link."""

    def test_cleartext_to_another_host_is_refused(self):
        with pytest.raises(ValueError, match="cleartext"):
            validate_endpoint("http://tutor.example:8000", require_tls_offbox=True)

    def test_https_to_another_host_is_fine(self):
        url = "https://tutor.example:8000"
        assert validate_endpoint(url, require_tls_offbox=True) == url

    @pytest.mark.parametrize(
        "url", ["http://localhost:11434", "http://127.0.0.1:8000", "http://[::1]:8000"]
    )
    def test_cleartext_to_loopback_is_fine(self, url):
        """Containers on one box already talk to each other this way."""
        assert validate_endpoint(url, require_tls_offbox=True) == url

    def test_the_rule_is_opt_in(self):
        """A LAN manifest fetch need not be TLS; a tutor link must be."""
        assert validate_endpoint("http://tutor.example", require_tls_offbox=False)
