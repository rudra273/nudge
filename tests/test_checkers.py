import json

import pytest
import requests

from nudge.checkers import CheckContext, get_checker
from nudge.models import Watch

URL = "https://example.com/page"


def run(web, check, params, previous=None):
    watch = Watch(id="w1", name="t", url=URL, check=check, params=params)
    return get_checker(check)(watch, CheckContext(previous=previous or {}, fetcher=web))


def test_keyword_found_with_snippet(web):
    web.set(URL, "<html><body><h1>Dune</h1><a>Book now</a><script>Book now</script></body></html>")
    r = run(web, "keyword", {"text": "book NOW"})
    assert r.matched and "Book now" in r.summary


def test_keyword_ignores_scripts(web):
    web.set(URL, "<html><body><p>Soon</p><script>var x='Book now'</script></body></html>")
    assert not run(web, "keyword", {"text": "Book now"}).matched


def test_keyword_absent_and_selector_scope(web):
    web.set(URL, "<div class='stock'>In stock</div><footer>Sold out items</footer>")
    assert run(web, "keyword", {"text": "Sold out", "absent": True, "selector": ".stock"}).matched
    assert not run(web, "keyword", {"text": "Sold out", "absent": True}).matched


def test_keyword_regex(web):
    web.set(URL, "<p>Only 3 left</p>")
    assert run(web, "keyword", {"regex": r"only \d+ left"}).matched


def test_status_up_down_and_unreachable(web):
    web.set(URL, "ok", status=200)
    assert run(web, "status", {}).matched
    web.set(URL, "nope", status=404)
    assert not run(web, "status", {}).matched
    assert run(web, "status", {"down": True}).matched
    assert run(web, "status", {"status": [404]}).matched
    web.pages[URL] = requests.ConnectionError("boom")
    assert run(web, "status", {"down": True}).matched
    assert not run(web, "status", {}).matched


def test_selector(web):
    web.set(URL, "<ul><li class='seat'>A1</li><li class='seat'>A2 VIP</li></ul>")
    assert run(web, "selector", {"selector": "li.seat", "min_count": 2}).matched
    assert not run(web, "selector", {"selector": "li.seat", "min_count": 3}).matched
    assert run(web, "selector", {"selector": "li.seat", "text": "vip"}).matched


@pytest.mark.parametrize("params,expected", [
    ({"path": "data.open", "op": "truthy"}, True),
    ({"path": "data.count", "op": "gt", "value": 4}, True),
    ({"path": "data.count", "op": "gt", "value": "5"}, False),
    ({"path": "data.items[1].name", "op": "eq", "value": "B"}, True),
    ({"path": "data.items.0.name", "op": "contains", "value": "a"}, True),
    ({"path": "data.items", "op": "len_gte", "value": 2}, True),
    ({"path": "data.nope", "op": "missing"}, True),
    ({"path": "data.nope", "op": "eq", "value": 1}, False),
    ({"path": "data.status", "op": "ne", "value": "CLOSED"}, False),
])
def test_json(web, params, expected):
    web.set(URL, json.dumps({"data": {"open": True, "count": 5, "status": "closed",
                                      "items": [{"name": "A"}, {"name": "B"}]}}), content_type="application/json")
    assert run(web, "json", params).matched is expected


def test_change_baseline_then_change(web):
    web.set(URL, "<main>v1</main><aside>ad 1</aside>")
    first = run(web, "change", {"selector": "main"})
    assert not first.matched and first.fingerprint
    web.set(URL, "<main>v1</main><aside>ad 2</aside>")
    assert not run(web, "change", {"selector": "main"}, {"fingerprint": first.fingerprint}).matched
    web.set(URL, "<main>v2</main>")
    second = run(web, "change", {"selector": "main"}, {"fingerprint": first.fingerprint})
    assert second.matched and not second.edge


def test_unknown_checker():
    with pytest.raises(ValueError):
        get_checker("nope")


def test_change_json_path(web):
    web.set(URL, json.dumps({"tag_name": "v1", "downloads": 1}), content_type="application/json")
    fp = run(web, "change", {"path": "tag_name"}).fingerprint
    web.set(URL, json.dumps({"tag_name": "v1", "downloads": 99}), content_type="application/json")
    assert not run(web, "change", {"path": "tag_name"}, {"fingerprint": fp}).matched
    web.set(URL, json.dumps({"tag_name": "v2"}), content_type="application/json")
    assert run(web, "change", {"path": "tag_name"}, {"fingerprint": fp}).matched
