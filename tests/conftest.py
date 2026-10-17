import pytest

from nudge.checkers.base import Response
from nudge.store import Store


class FakeWeb:
    """Stands in for http_fetch: serve canned responses per URL."""

    def __init__(self):
        self.pages: dict[str, Response] = {}
        self.calls: list[str] = []

    def set(self, url, text, status=200, content_type="text/html"):
        self.pages[url] = Response(status=status, text=text, url=url, headers={"Content-Type": content_type})

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        if isinstance(self.pages.get(url), Exception):
            raise self.pages[url]
        return self.pages[url]


@pytest.fixture
def web():
    return FakeWeb()


@pytest.fixture
def store(tmp_path):
    return Store.load(tmp_path / "watches.yaml", tmp_path / "state.json")
