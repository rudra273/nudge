"""Small District movie pages shaped like the live __NEXT_DATA__ (trimmed to the fields Nudge reads)."""

import json
from datetime import datetime, timedelta

GROUP_TA = "grpta000001"
GROUP_HI = "grphi000002"


def utc(ist: str) -> str:
    """District's showTime is UTC; tests write the IST time a moviegoer sees."""
    return (datetime.fromisoformat(ist) - timedelta(hours=5, minutes=30)).strftime("%Y-%m-%dT%H:%M")


def session(cid, when, sid, fmt="2D", avail=100, total=200, status="Available", premium="", group=GROUP_TA):
    return {"sid": str(sid), "cid": cid, "showTime": utc(when), "scrnFmt": fmt, "premiumLabel": premium, "audi": "AUDI 1",
            "avail": avail, "total": total, "seatStatus": status, "mcd": group, "encSessionId": f"{cid}-{sid}-x-{cid}"}


def cinema(cid, name, chain, sessions):
    return {"id": cid, "cinemaInfo": {"name": name, "label": name, "chainKey": chain}, "sessions": sessions}


def page(*, show_dates=(), release="2026-12-18", cinemas=(), group=GROUP_TA, search_date="",
         groups=((GROUP_TA, "Tamil"),), title="Test Movie"):
    """A movie page: `cinemas` are the shows for `search_date` (or the first open date) in `group`."""
    sessions = {}
    if show_dates:
        sessions[group + search_date] = {
            "searchDate": search_date,
            "meta": {"showDates": list(show_dates), "movie": {"name": title, "release_date": release}},
            "pageData": {"nearbyCinemas": list(cinemas), "farCinemas": []},
        }
    data = {"props": {"pageProps": {"data": {"serverState": {
        "movieContents": {"1": {"name": title, "releaseDate": release, "isReleased": bool(show_dates),
                                "languageFormatGroups": [{"language": lang, "format": g} for g, lang in groups]}},
        "movieData": {"1": {"movie": {"name": title, "release_date": release}, "showDates": list(show_dates)}},
        "movieSessions": sessions,
    }}}}}
    return (f"<html><head><title>Book {title} Tickets in Bengaluru - District</title></head><body>"
            f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></body></html>')
