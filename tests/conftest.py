import base64
from pathlib import Path

import pytest

from newsflow.config import load_config
from newsflow.http import FetchError

FIXTURES = Path(__file__).parent / "fixtures"
REPO = Path(__file__).parent.parent


def google_token(url: str) -> str:
    """Build an old-style Google News article token whose base64 payload contains the URL."""
    payload = b"\x08\x13\x22" + bytes([len(url)]) + url.encode() + b"\xd2\x01\x00"
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


@pytest.fixture
def googlenews_xml() -> str:
    text = (FIXTURES / "googlenews_sv.xml").read_text(encoding="utf-8")
    return text.replace("{TOKEN_PLACERA}", google_token("https://www.placera.se/nyheter/intrum-emitterar-nya-obligationer-2026-07-06")).replace(
        "{TOKEN_KL}", google_token("https://www.kauppalehti.fi/porssi/porssikurssit/osake/XSTO/INTRUM/osinkohistoria")
    )


@pytest.fixture
def bing_xml() -> str:
    return (FIXTURES / "bing_sv.xml").read_text(encoding="utf-8")


@pytest.fixture
def gdelt_json() -> str:
    return (FIXTURES / "gdelt.json").read_text(encoding="utf-8")


@pytest.fixture
def outlet_feed_xml() -> str:
    return (FIXTURES / "outlet_feed.xml").read_text(encoding="utf-8")


@pytest.fixture
def tagpage_run1() -> str:
    return (FIXTURES / "tagpage_run1.html").read_text(encoding="utf-8")


@pytest.fixture
def tagpage_run2() -> str:
    return (FIXTURES / "tagpage_run2.html").read_text(encoding="utf-8")


@pytest.fixture
def cfg():
    return load_config(REPO / "config")


class FakeHttp:
    """Serves canned responses by URL substring; raises FetchError otherwise."""

    def __init__(self, responses: dict[str, str]):
        self.responses = responses
        self.calls: list[str] = []

    def _lookup(self, url: str) -> str:
        self.calls.append(url)
        for key, text in self.responses.items():
            if key in url:
                return text
        raise FetchError(f"no fixture for {url}")

    def get_text(self, url: str, *, is_page: bool = False, accept: str = "") -> str:
        return self._lookup(url)

    def head_final_url(self, url: str) -> str:
        return url

    def allowed_by_robots(self, url: str) -> bool:
        return True

    def close(self) -> None:
        pass


@pytest.fixture
def fake_http_factory():
    return FakeHttp
