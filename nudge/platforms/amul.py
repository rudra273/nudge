"""Amul shop (shop.amul.com) stock checker: protein products and anything else, by pincode.

Uses the same JSON API as the website, so no scraping and no AI. Stock is per region,
which the site picks from your pincode.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from typing import Any

import requests

from nudge.checkers import checker
from nudge.checkers.base import BlockedError, CheckContext, CheckResult, FetchError

BASE = "https://shop.amul.com"
PRODUCT_URL = BASE + "/en/product/{alias}"
STOPWORDS = {"amul", "the", "a", "an", "of", "and", "pack", "is", "in", "stock", "back", "when", "available",
             "restock", "restocked", "notify", "me", "tell", "alert", "for", "at", "pincode", "pin", "my",
             "products", "product", "every", "once", "hourly", "daily", "comes", "gets", "get"}


class AmulClient:
    def __init__(self, session: requests.Session, timeout: float = 20.0):
        self.s = session
        self.timeout = timeout
        self.s.headers.setdefault("Accept", "application/json, text/plain, */*")
        self.s.headers.update({"Origin": BASE, "Referer": BASE + "/en/browse/protein"})
        self._ts = self._tid = self._store = None

    def _get(self, path: str, **kwargs) -> requests.Response:
        r = self.s.get(BASE + path, timeout=self.timeout, **kwargs)
        if r.status_code in (403, 503) and "cloudflare" in r.headers.get("server", "").lower():
            raise BlockedError(f"Amul shop blocked the request (HTTP {r.status_code})")
        if r.status_code >= 400:
            raise FetchError(f"Amul shop returned HTTP {r.status_code} for {path}")
        return r

    def _bootstrap(self) -> None:
        html = self._get("/en/browse/protein").text
        ts = re.search(r'serverTimestamp\s*=\s*"(\d+)"', html)
        info = self._get("/user/info.js").text
        store = re.search(r'"storeId"\s*:\s*"([0-9a-f]+)"', self._get("/ms/store/amul/auto/EN/storeinfo.js").text)
        try:
            tid = json.loads(info.split("=", 1)[1].strip().rstrip(";"))["tid"]
        except (IndexError, ValueError, KeyError):
            tid = None
        if not (ts and store and tid):
            raise FetchError("Amul shop changed its page format; the Amul checker needs an update")
        self._ts, self._tid, self._store = ts.group(1), tid, store.group(1)

    def _headers(self) -> dict[str, str]:
        if self._ts is None:
            self._bootstrap()
        n = random.randint(0, 999)
        digest = hashlib.sha256(f"{self._store}:{self._ts}:{n}:{self._tid}".encode()).hexdigest()
        return {"tid": f"{self._ts}:{n}:{digest}", "frontend": "1", "base_url": BASE + "/en/browse/protein"}

    def set_pincode(self, pincode: str) -> str:
        r = self._get("/entity/pincode", headers=self._headers(), params={
            "limit": 50, "filters[0][field]": "pincode", "filters[0][value]": pincode,
            "filters[0][operator]": "regex", "cf_cache": "1h"})
        records = r.json().get("records") or []
        if not records:
            raise FetchError(f"Amul doesn't deliver to pincode {pincode}")
        substore = records[0]["substore"]
        resp = self.s.put(BASE + "/entity/ms.settings/_/setPreferences", json={"data": {"store": substore}},
                          headers=self._headers(), timeout=self.timeout)
        if resp.status_code >= 400:
            raise FetchError(f"Amul shop refused the pincode (HTTP {resp.status_code})")
        return substore

    def products(self, category: str | None = "protein", search: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": 100, "start": 0, "total": 1}
        for f in ("name", "alias", "price", "available", "inventory_quantity", "categories"):
            params[f"fields[{f}]"] = 1
        if category:
            params.update({"filters[0][field]": "categories", "filters[0][value][0]": category,
                           "filters[0][operator]": "in"})
        if search:
            params["q"] = search
        return self._get("/api/1/entity/ms.products", headers=self._headers(), params=params).json().get("data") or []


def matches(product_name: str, query: str | None) -> bool:
    if not query:
        return True
    name = product_name.lower()
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if w not in STOPWORDS]
    return all(w in name for w in words)


@checker("amul")
def check_amul(watch, ctx: CheckContext) -> CheckResult:
    """Alert when a matching Amul product is in stock for your pincode.

    params: pincode (required), product (words that must all be in the name, e.g. "whey chocolate";
    empty = every product in the category), category (default "protein"; use "" for the whole shop).
    Notifies whenever a matching product comes into stock that wasn't in stock at the last check.
    """
    p = watch.params
    pincode = str(p.get("pincode") or "").strip()
    if not re.fullmatch(r"\d{6}", pincode):
        raise ValueError("amul check needs a 6-digit params.pincode")
    client = AmulClient(ctx.session(), timeout=ctx.timeout)
    region = client.set_pincode(pincode)
    category = p.get("category", "protein") or None
    items = [x for x in client.products(category) if matches(x.get("name", ""), p.get("product"))]
    if not items:
        raise FetchError(f"No Amul products match {p.get('product')!r} in category {category!r}")
    in_stock = [x for x in items if int(x.get("available") or 0) > 0]
    before = set(ctx.memo.get("in_stock", []))
    ctx.memo["in_stock"] = sorted(x["alias"] for x in in_stock)
    new = [x for x in in_stock if x["alias"] not in before]
    fingerprint = hashlib.sha256(",".join(ctx.memo["in_stock"]).encode()).hexdigest()

    def line(x: dict) -> str:
        return f"• {x['name']} — ₹{x.get('price')} ({PRODUCT_URL.format(alias=x['alias'])})"

    details = {"region": region, "matching": len(items), "in_stock": [x["alias"] for x in in_stock]}
    if new:  # includes the first check: if it's already in stock, you want to know now
        shown = "\n".join(line(x) for x in new[:8])
        more = f"\n…and {len(new) - 8} more" if len(new) > 8 else ""
        link = PRODUCT_URL.format(alias=new[0]["alias"]) if len(new) == 1 else BASE + "/en/browse/protein"
        return CheckResult(True, f"In stock at {pincode} ({len(in_stock)}/{len(items)} matching):\n{shown}{more}",
                           fingerprint=fingerprint, edge=False, details=details, link=link)
    if in_stock:
        return CheckResult(False, f"{len(in_stock)}/{len(items)} matching in stock at {pincode} (no change)",
                           fingerprint=fingerprint, edge=False, details=details)
    return CheckResult(False, f"All {len(items)} matching products out of stock at {pincode}",
                       fingerprint=fingerprint, edge=False, details=details)


def parse(text: str) -> dict[str, Any] | None:
    lowered = text.lower()
    if "amul" not in lowered:
        return None
    pin = re.search(r"\b(\d{6})\b", text)
    m = re.search(r"amul\s+(.*?)(?=\s+(?:is|are|gets|comes|back|in stock|at|for|pincode|pin|every|hourly|daily|once|"
                  r"when|available|restock)\b|[.,!?]|$)", text, re.I)
    product = (m.group(1).strip() if m else "") or ""
    product = " ".join(w for w in re.findall(r"[\w-]+", product) if w.lower() not in STOPWORDS)
    category = "protein" if ("protein" in lowered or not product) else ""
    if product.lower() == "protein":
        product = ""
    params: dict[str, Any] = {"pincode": pin.group(1) if pin else ""}
    if product:
        params["product"] = product
    if category != "protein":
        params["category"] = category
    label = f"Amul {product or 'protein'}".strip()
    return {"url": BASE + "/en/browse/protein", "check": "amul", "params": params,
            "name": f"{label} in stock" + (f" at {params['pincode']}" if params["pincode"] else ""),
            "needs": [] if pin else ["pincode"]}
