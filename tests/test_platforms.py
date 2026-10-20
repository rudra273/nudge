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
     {"movie": "Jailer 2", "city": "bengaluru"}, "2m"),
    ("notify me when Jailer 2 tickets open on BookMyShow in Mumbai", "bookmyshow",
     {"movie": "Jailer 2", "city": "mumbai"}, "5m"),
    ("tell me when the tickets for Dune Part Three open on bms in hyderabad every 2 min", "bookmyshow",
     {"movie": "Dune Part Three", "city": "hyderabad"}, "2m"),
    ("The Batman bookings on book my show in gurgaon", "bookmyshow", {"movie": "The Batman", "city": "delhi"}, "5m"),
    ("Jailer 2 on district at PVR Orion in Bengaluru", "district",
     {"movie": "Jailer 2", "city": "bengaluru", "cinema": "PVR Orion"}, "2m"),
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


# -- District: the embedded schedule (__NEXT_DATA__) ---------------------------------

from fixtures.district_next_data import GROUP_HI, GROUP_TA, cinema, page, session  # noqa: E402

from nudge.platforms import district  # noqa: E402
from nudge.platforms.common import extract_movie_filters, match_cinema  # noqa: E402

MV = f"{D}/movies/test-movie-movie-tickets-in-bengaluru-MV176735"
DAY1, DAY2 = "2026-12-18", "2026-12-19"
ORION = cinema(1, "PVR Orion Mall, Dr Rajkumar Road, Bengaluru", "PVR",
               [session(1, f"{DAY1}T06:00", 11, fmt="IMAX 3D", avail=12, total=180, status="Filling Fast"),
                session(1, f"{DAY1}T12:30", 12), session(1, f"{DAY1}T09:30", 13)])
LIDO = cinema(2, "INOX Lido Mall, Ulsoor, Bengaluru", "PVR",  # District files INOX under the PVR chain
              [session(2, f"{DAY1}T07:00", 21, avail=0, status="Sold Out")])
CINEPOLIS = cinema(3, "Cinepolis Orion Avenue Mall, Banaswadi, Bengaluru", "Cinepolis",
                   [session(3, f"{DAY2}T10:00", 31, fmt="4DX")])


def district_watch(**params):
    return Watch(id="d", name="Test", url=MV, check="district", params=params)


def test_district_schedule_upcoming_shows_release_date(web):
    web.set(MV, page())
    r = get_checker("district")(district_watch(), ctx(web))
    assert not r.matched and r.summary == "No shows yet (releasing Fri 18 Dec)"


def test_district_schedule_alerts_with_grouped_shows_and_seat_link(web):
    web.set(MV, page(show_dates=[DAY1], cinemas=[ORION, LIDO]))
    r = get_checker("district")(district_watch(), ctx(web))
    assert r.matched and not r.edge
    lines = r.summary.splitlines()
    assert lines[0] == "Test Movie: bookings open!" and lines[1] == "📅 Fri 18 Dec"
    assert "• PVR Orion Mall, Dr Rajkumar Road: 06:00 AM IMAX 3D (12/180 left, filling fast), 09:30 AM, 12:30 PM" in lines
    assert "• INOX Lido Mall, Ulsoor: 07:00 AM (sold out)" in lines
    # the earliest show that isn't sold out, straight to seat selection
    assert r.link == (f"{D}/movies/seat-layout/{GROUP_TA}?encsessionid=1-11-x-1&freeseating=false"
                      "&fromsessions=true&type=MOVIES&contentid=176735")


def test_district_updates_alert_only_on_new_days_and_cinemas(web):
    memo = {}
    web.set(MV, page(show_dates=[DAY1], cinemas=[ORION]))
    assert get_checker("district")(district_watch(), ctx(web, memo)).matched
    again = get_checker("district")(district_watch(), ctx(web, memo))
    assert again.matched and again.edge and "no new days or cinemas" in again.summary  # stays ✅, no re-alert
    web.set(MV, page(show_dates=[DAY1], cinemas=[ORION, LIDO]))
    r = get_checker("district")(district_watch(), ctx(web, memo))
    assert r.matched and "more shows open" in r.summary and "INOX Lido" in r.summary and "Orion" not in r.summary


def test_district_show_times_are_ist_not_utc(web):
    # showTime 2026-12-17T22:30 is UTC: a 4 AM first-day show on the 18th in India
    early = cinema(1, "PVR Orion", "PVR", [session(1, f"{DAY1}T04:00", 1)])
    assert early["sessions"][0]["showTime"] == "2026-12-17T22:30"
    web.set(MV, page(show_dates=[DAY1], release=DAY1, cinemas=[early]))
    r = get_checker("district")(district_watch(date="release"), ctx(web))
    assert r.matched and "📅 Fri 18 Dec" in r.summary and "04:00 AM" in r.summary


def test_district_updates_off_is_a_plain_edge_alert(web):
    web.set(MV, page(show_dates=[DAY1], cinemas=[ORION]))
    r = get_checker("district")(district_watch(updates=False), ctx(web))
    assert r.matched and r.edge


def test_district_release_date_filter_fetches_only_open_days(web):
    web.set(MV, page(show_dates=["2026-12-17"], release=DAY1,
                     cinemas=[cinema(1, "PVR Orion", "PVR", [session(1, "2026-12-17T21:00", 1)])]))
    r = get_checker("district")(district_watch(date="release"), ctx(web))
    assert not r.matched and "not yet for Fri 18 Dec" in r.summary
    assert web.calls == [MV]  # a closed day costs no request
    web.set(MV, page(show_dates=["2026-12-17", DAY1], release=DAY1,
                     cinemas=[cinema(1, "PVR Orion", "PVR", [session(1, "2026-12-17T21:00", 1)])]))
    web.set(f"{MV}?frmtid={GROUP_TA}&fromdate={DAY1}", page(show_dates=["2026-12-17", DAY1], release=DAY1,
                                                            cinemas=[ORION], search_date=DAY1))
    r = get_checker("district")(district_watch(date="release"), ctx(web))
    assert r.matched and "Fri 18 Dec" in r.summary and "Thu 17 Dec" not in r.summary


def test_district_cinema_format_and_language_filters(web):
    web.set(MV, page(show_dates=[DAY1, DAY2], cinemas=[ORION, LIDO]))
    web.set(f"{MV}?frmtid={GROUP_TA}&fromdate={DAY2}", page(show_dates=[DAY1, DAY2], cinemas=[CINEPOLIS],
                                                            search_date=DAY2))
    check = get_checker("district")
    r = check(district_watch(cinema="PVR"), ctx(web))
    assert r.matched and "PVR Orion" in r.summary and "INOX" not in r.summary  # chain key is ignored
    r = check(district_watch(cinema=["inox lido", "cinepolis orion"]), ctx(web))
    assert "INOX Lido" in r.summary and "Cinepolis Orion Avenue Mall" in r.summary and "PVR" not in r.summary
    r = check(district_watch(format="IMAX"), ctx(web))
    assert r.matched and "06:00 AM IMAX 3D" in r.summary and "09:30" not in r.summary
    assert "4DX" in check(district_watch(format="4dx"), ctx(web)).summary
    r = check(district_watch(format="Dolby Atmos"), ctx(web))
    assert not r.matched and "no shows matching your filters" in r.summary

    two = ((GROUP_TA, "Tamil"), (GROUP_HI, "Hindi"))
    web.set(MV, page(show_dates=[DAY1], cinemas=[ORION], groups=two))
    web.set(f"{MV}?frmtid={GROUP_HI}&fromdate={DAY1}", page(
        show_dates=[DAY1], groups=two, group=GROUP_HI, search_date=DAY1,
        cinemas=[cinema(4, "PVR Forum", "PVR", [session(4, f"{DAY1}T08:00", 41, group=GROUP_HI)])]))
    r = check(district_watch(language="Hindi"), ctx(web))
    assert r.matched and "PVR Forum" in r.summary and "Orion" not in r.summary


def test_match_cinema():
    assert match_cinema("PVR Orion", "PVR: Orion Mall, Dr Rajkumar Road")
    assert match_cinema("pvr", "PVR Nexus (Formerly Forum), Koramangala")
    assert match_cinema("any cinepolis", "Cinépolis Orion Avenue Mall")
    assert not match_cinema("PVR Orion", "INOX Lido Mall")


def test_find_movies_offers_every_match(web):
    web.set(f"{D}/movies/upcoming-movies-in-bengaluru", DISTRICT_LISTING)
    found = district.find_movies(ctx(web), "Jailer", "bengaluru")
    assert found[0].endswith("jailer-movie-tickets-in-bengaluru-MV100001")  # exact title wins
    web.set(f"{D}/movies/", "<html></html>")
    found = district.find_movies(ctx(web), "Varanasi", "bengaluru")
    assert found == [f"{D}/movies/varanasi-movie-tickets-in-bengaluru-MV209276"]


@pytest.mark.parametrize("text,params", [
    ("Avengers Doomsday IMAX FDFS on District at any PVR in Bangalore",
     {"movie": "Avengers Doomsday", "city": "bengaluru", "cinema": "PVR", "date": "release", "format": "IMAX"}),
    ('"Jailer 2" Tamil 4DX on district at PVR Orion or INOX Garuda in chennai on 18 Dec, urgent',
     {"movie": "Jailer 2", "city": "chennai", "cinema": ["PVR Orion", "INOX Garuda"], "date": "2026-12-18",
      "format": "4DX", "language": "Tamil", "urgent": True}),
    ("Ice Age on district at \"Sathyam Cinemas\" in Chennai first day first show",
     {"movie": "Ice Age", "city": "chennai", "cinema": "Sathyam Cinemas", "date": "release"}),
    ("Leo on district at Rohini Silver Screens in Chennai",
     {"movie": "Leo", "city": "chennai", "cinema": "Rohini Silver Screens"}),
])
def test_district_movie_requests(text, params, monkeypatch):
    from datetime import date

    monkeypatch.setattr("nudge.platforms.common.today_ist", lambda: date(2026, 10, 18))
    w = nlp.parse(text)
    assert (w.check, w.params, w.interval) == ("district", params, "2m")


def test_movie_filter_dates():
    from datetime import date

    today = date(2026, 10, 18)  # a Sunday
    assert extract_movie_filters("x on friday", today)[0] == {"date": "2026-10-23"}
    assert extract_movie_filters("x for 2 jan", today)[0] == {"date": "2027-01-02"}  # next occurrence
    assert extract_movie_filters("x on Dec 24th 2026", today)[0] == {"date": "2026-12-24"}
    assert extract_movie_filters("x on 25/12", today)[0] == {"date": "2026-12-25"}
    assert extract_movie_filters("x tomorrow", today)[0] == {"date": "2026-10-19"}
    params, rest = extract_movie_filters('"Ice Age" in ICE and Dolby Atmos', today)
    assert params == {"format": ["Dolby Atmos", "ICE"]} and '"Ice Age"' in rest


def test_district_defaults_fill_city():
    w = nlp.parse("Doomsday IMAX FDFS on District", defaults={"city": "bengaluru"})
    assert w.params["city"] == "bengaluru" and "(bengaluru)" in w.name
    with pytest.raises(nlp.MissingInfo) as exc:
        nlp.parse("Doomsday on District")
    assert exc.value.need == "city"
