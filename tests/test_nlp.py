import pytest

from nudge import nlp


def p(text):
    return nlp.parse(text)


def test_keyword_quoted_with_interval():
    w = p('Tell me when https://tickets.example.com/dune says "Book now" every 5 minutes')
    assert (w.url, w.check, w.params, w.interval) == ("https://tickets.example.com/dune", "keyword",
                                                      {"text": "Book now"}, "5m")


def test_restock_absent():
    w = p('notify me when https://shop.example.com/x no longer says "Sold out", hourly')
    assert w.check == "keyword" and w.params == {"text": "Sold out", "absent": True} and w.interval == "1h"


def test_unquoted_keyword():
    w = p("ping me when https://example.com/concerts mentions Coldplay")
    assert w.check == "keyword" and w.params["text"] == "Coldplay"


def test_status_and_down():
    assert p("tell me when https://new-site.example.com goes live").check == "status"
    w = p("alert me if https://api.example.com is down every 2 min")
    assert w.check == "status" and w.params == {"down": True} and w.interval == "2m"


def test_change_with_selector():
    w = p("watch https://example.com/blog `article h2` for changes daily")
    assert w.check == "change" and w.params == {"selector": "article h2"} and w.interval == "1d"


def test_change_not_mistaken_for_keyword():
    assert p("tell me when https://example.com/releases has changed").check == "change"


def test_selector_only():
    w = p("tell me when https://example.com/shop has `button.add-to-cart`")
    assert w.check == "selector" and w.params == {"selector": "button.add-to-cart"}


def test_json_field():
    w = p("notify me when https://api.example.com/v1/slots field data.available > 0 every 10m")
    assert w.check == "json" and w.params == {"path": "data.available", "op": "gt", "value": 0}
    w = p("tell me when https://api.example.com/reg field open is true, once")
    assert w.params == {"path": "open", "op": "truthy"} and w.notify_once


def test_cron():
    w = p('check https://example.com says "Open" cron "0 9 * * 1-5"')
    assert w.cron == "0 9 * * 1-5" and w.interval is None


def test_no_url():
    with pytest.raises(nlp.ParseError):
        p("tell me when taylor swift tickets drop")


def test_describe():
    w = p('when https://x.com no longer says "Sold out" once')
    assert nlp.describe(w) == 'when "Sold out" disappears (every 15m, once)'


def test_interval_has_a_floor():
    assert p('https://x.com says "hi" every 10 seconds').interval == "1m"
