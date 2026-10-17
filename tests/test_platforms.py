import json

import pytest

from nudge import nlp
from nudge.checkers import CheckContext, get_checker
from nudge.checkers.base import BlockedError, Response
from nudge.engine import run_watch
from nudge.models import Watch

D = "https://www.district.in"

DISTRICT_LISTING = """<html><body><script>self.__next_f.push([1,"{\\"href\\":\\"/movies/jailer-2-movie-tickets-in-bengaluru-MV187766\\"}"])</script>
<a href="/movies/jailer-movie-tickets-in-bengaluru-MV100001">Jailer</a>
<a href="/movies/varanasi-movie-tickets-in-bengaluru-MV209276">Varanasi</a></body></html>"""
DISTRICT_OPEN = """<html><head><title>Book Jailer 2 Tickets in Bengaluru - Showtimes - District</title></head><body>
<h1>Jailer 2</h1><div>OCT 17 Sat 18 Sun</div>
<div>PVR Orion Mall, Dr Rajkumar Road, Bengaluru Non-cancellable <span>03:30 PM</span> PXL <span>06:55 PM</span> GOLD</div>
<div>INOX Megaplex Mall of Asia, Byatarayanapura, Bengaluru Non-cancellable <span>03:25 PM</span></div>
<p>Where can I watch Jailer 2 in Bengaluru?</p><p>Movies Now Showing in Bengaluru 11:00 AM</p></body></html>"""
DISTRICT_UPCOMING = """<html><head><title>Varanasi (2027) - Cast, Reviews &amp; Showtimes - District</title></head>
<body><h1>Varanasi</h1><p>Action Adventure Epic Releasing 7 April 2027</p><p>Trailers 3:40</p>
<p>Movies Now Showing in Bengaluru 10:00 PM</p></body></html>"""


def ctx(web, memo=None, previous=None, session=None):
    return CheckContext(previous=previous or {}, fetcher=web, memo=memo if memo is not None else {},
                        session_factory=session)


# -- District ------------------------------------------------------------------

def test_district_resolves_title_and_detects_showtimes(web):
    web.set(f"{D}/movies/upcoming-movies-in-bengaluru", DISTRICT_LISTING)
    web.set(f"{D}/movies/jailer-2-movie-tickets-in-bengaluru-MV187766", DISTRICT_OPEN)
    w = Watch(id="d", name="J2", url=f"{D}/movies/", check="district", params={"movie": "Jailer 2", "city": "bangalore"})
    memo = {}
    r = get_checker("district")(w, ctx(web, memo))
    assert r.matched and "03:30 PM" in r.summary and "11:00 AM" not in r.summary
    assert r.link == memo["url"] == f"{D}/movies/jailer-2-movie-tickets-in-bengaluru-MV187766"


def test_district_cinema_filter_stops_at_next_cinema(web):
    web.set(f"{D}/movies/jailer-2-movie-tickets-in-bengaluru-MV187766", DISTRICT_OPEN)
    url = f"{D}/movies/jailer-2-movie-tickets-in-bengaluru-MV187766"
    r = get_checker("district")(Watch(id="d", name="x", url=url, check="district", params={"cinema": "pvr orion"}),
                                ctx(web))
    assert r.matched and "06:55 PM" in r.summary and "03:25 PM" not in r.summary
    r = get_checker("district")(Watch(id="d", name="x", url=url, check="district", params={"cinema": "Cinepolis"}),
                                ctx(web))
    assert not r.matched


def test_district_upcoming_and_unlisted(web):
    web.set(f"{D}/movies/upcoming-movies-in-bengaluru", DISTRICT_LISTING)
    web.set(f"{D}/movies/", "<html></html>")
    web.set(f"{D}/movies/varanasi-movie-tickets-in-bengaluru-MV209276", DISTRICT_UPCOMING)
    w = Watch(id="d", name="V", url=f"{D}/movies/", check="district", params={"movie": "Varanasi", "city": "bengaluru"})
    r = get_checker("district")(w, ctx(web))
    assert not r.matched and "Releasing 7 April 2027" in r.summary
    w.params["movie"] = "Some Unknown Film"
    assert "isn't listed" in get_checker("district")(w, ctx(web)).summary


# -- BookMyShow ------------------------------------------------------------------

def test_bookmyshow_now_showing_and_blocked(web):
    web.set("https://in.bookmyshow.com/explore/movies-mumbai",
            '<a href="/movies/mumbai/jailer-2/ET00412345">Jailer 2</a>')
    w = Watch(id="b", name="J2", url="https://in.bookmyshow.com/", check="bookmyshow",
              params={"movie": "Jailer 2", "city": "bombay"})
    r = get_checker("bookmyshow")(w, ctx(web))
    assert r.matched and r.link == "https://in.bookmyshow.com/movies/mumbai/jailer-2/ET00412345"

    web.pages["https://in.bookmyshow.com/explore/movies-mumbai"] = Response(
        403, "<title>Attention Required! | Cloudflare</title>Sorry, you have been blocked", "u", {"Server": "cloudflare"})
    with pytest.raises(BlockedError):
        get_checker("bookmyshow")(w, ctx(web))


def test_bookmyshow_movie_page(web):
    url = "https://in.bookmyshow.com/movies/bengaluru/jailer-2/ET00412345"
    web.set(url, "<button>I'm interested</button>")
    w = Watch(id="b", name="x", url=url, check="bookmyshow")
    assert not get_checker("bookmyshow")(w, ctx(web)).matched
    web.set(url, '<a href="/buytickets/jailer-2-bengaluru/movie-bang-ET00412345-MT/20261018">Book tickets</a>')
    assert get_checker("bookmyshow")(w, ctx(web)).matched


def test_blocked_page_never_counts_as_absent_text(store, web):
    """A Cloudflare wall has no "Sold out" on it; that must be an error, not a restock alert."""
    url = "https://in.bookmyshow.com/events/x/ET1"
    web.pages[url] = Response(403, "Just a moment...", url, {"server": "cloudflare"})
    w = store.add(Watch(id="k", name="k", url=url, check="keyword", params={"text": "Sold out", "absent": True}))
    out = run_watch(store, w, None, fetcher=web)
    assert out.error and "Cloudflare" in out.error and not out.notified


# -- generic booking ---------------------------------------------------------------

@pytest.mark.parametrize("html,expected", [
    ("<button>Book tickets</button>", True),
    ("<a class='cta'>Buy Now</a>", True),
    ("<button disabled>Coming Soon</button>", False),
    ("<button>Sold out</button><p>Book tickets for other events</p>", False),
    ("<p>Book tickets for the latest movies</p>", False),
    ("<nav><a>Register</a></nav>", False),
])
def test_booking_button(web, html, expected):
    web.set("https://t.example/e", html)
    w = Watch(id="e", name="e", url="https://t.example/e", check="booking")
    assert get_checker("booking")(w, ctx(web)).matched is expected


# -- Amul --------------------------------------------------------------------------

class FakeAmul:
    """Mimics shop.amul.com's endpoints."""

    def __init__(self, stock):
        self.stock = stock
        self.headers = {}
        self.puts = []

    def _resp(self, status=200, text="", data=None):
        class R:
            pass
        r = R()
        r.status_code, r.headers = status, {}
        r.text = text if data is None else json.dumps(data)
        r.json = lambda: json.loads(r.text)
        return r

    def get(self, url, headers=None, params=None, timeout=None):
        if url.endswith("/en/browse/protein"):
            return self._resp(text='<script>serverTimestamp = "1792232549892"</script>')
        if url.endswith("/user/info.js"):
            return self._resp(text='session = {"tid":"abc123"}')
        if url.endswith("storeinfo.js"):
            return self._resp(text='x={"storeId":"62fa94df8c13af2e242eba16"}')
        if url.endswith("/entity/pincode"):
            assert headers["tid"].startswith("1792232549892:")
            ok = params["filters[0][value]"] == "560001"
            return self._resp(data={"records": [{"pincode": "560001", "substore": "karnataka"}] if ok else []})
        if url.endswith("/api/1/entity/ms.products"):
            return self._resp(data={"data": [{"name": n, "alias": n.lower().replace(" ", "-"), "price": 100,
                                              "available": a} for n, a in self.stock.items()]})
        raise AssertionError(url)

    def put(self, url, json=None, headers=None, timeout=None):
        self.puts.append(json)
        return self._resp(text="Updated successfully")


def test_amul_alerts_on_new_stock_only(store, monkeypatch):
    fake = FakeAmul({"Amul Whey Protein 30 sachets": 0, "Amul Chocolate Whey Protein": 0, "Amul Protein Lassi": 1})
    monkeypatch.setattr(CheckContext, "session", lambda self: fake)
    w = store.add(Watch(id="am", name="Whey", url="https://shop.amul.com/", check="amul",
                        params={"pincode": "560001", "product": "whey"}))
    sent = []

    def run():
        return run_watch(store, w, sent.append)

    assert not run().notified and "out of stock" in store.state_for(w)["summary"]
    assert fake.puts[-1] == {"data": {"store": "karnataka"}}
    fake.stock["Amul Chocolate Whey Protein"] = 1
    assert run().notified and "Chocolate Whey" in sent[-1] and "amul-chocolate-whey-protein" in sent[-1]
    assert not run().notified  # still in stock: no repeat
    fake.stock["Amul Whey Protein 30 sachets"] = 1
    assert run().notified and "30 sachets" in sent[-1]


def test_amul_bad_pincode(store):
    fake = FakeAmul({})
    w = Watch(id="am", name="x", url="https://shop.amul.com/", check="amul", params={"pincode": "999999"})
    with pytest.raises(Exception, match="doesn't deliver"):
        get_checker("amul")(w, CheckContext(previous={}, session_factory=lambda: fake))


# -- parsing (no AI) -----------------------------------------------------------------

@pytest.mark.parametrize("text,check,params,interval", [
    ('tell me when "Jailer 2" bookings open on District in Bangalore', "district",
     {"movie": "Jailer 2", "city": "bengaluru"}, "5m"),
    ("notify me when Jailer 2 tickets open on BookMyShow in Mumbai", "bookmyshow",
     {"movie": "Jailer 2", "city": "mumbai"}, "5m"),
    ("tell me when the tickets for Dune Part Three open on bms in hyderabad every 2 min", "bookmyshow",
     {"movie": "Dune Part Three", "city": "hyderabad"}, "2m"),
    ("The Batman bookings on book my show in gurgaon", "bookmyshow", {"movie": "The Batman", "city": "delhi"}, "5m"),
    ("Jailer 2 on district at PVR Orion in Bengaluru", "district",
     {"movie": "Jailer 2", "city": "bengaluru", "cinema": "PVR Orion"}, "5m"),
    ("amul whey protein back in stock at 560001", "amul", {"pincode": "560001", "product": "whey protein"}, "5m"),
    ("notify when amul protein is in stock pincode 400001 every 10 min", "amul", {"pincode": "400001"}, "10m"),
])
def test_platform_parsing(text, check, params, interval):
    w = nlp.parse(text)
    assert (w.check, w.params, w.interval) == (check, params, interval)


@pytest.mark.parametrize("text,needle", [
    ("tell me when amul chocolate whey is available", "pincode"),
    ("district jailer 2", "city"),
    ("tell me when coldplay tickets drop", "link"),
])
def test_platform_parsing_asks_for_missing_info(text, needle):
    with pytest.raises(nlp.ParseError, match=needle):
        nlp.parse(text)


def test_district_and_bms_urls():
    w = nlp.parse("https://www.district.in/events/a-night-of-dandiya-oct20-2026-buy-tickets")
    assert w.check == "booking" and w.interval == "10m"
    w = nlp.parse("watch https://www.district.in/movies/jailer-2-movie-tickets-in-bengaluru-MV187766")
    assert w.check == "district"
    w = nlp.parse("https://in.bookmyshow.com/movies/bengaluru/jailer-2/ET00412345 once")
    assert w.check == "bookmyshow" and w.notify_once
