"""Example plugin: a custom checker.

Use it with:  NUDGE_PLUGINS=my_checkers  (make sure this file is importable, e.g. PYTHONPATH=examples)

    - name: Cheap flight
      url: https://api.example.com/fares?from=BLR&to=GOA
      check: price_below
      params: {path: data.cheapest.price, max: 3000}
"""

from nudge.checkers import CheckResult, checker
from nudge.checkers.builtin import _dig


@checker("price_below")
def price_below(watch, ctx):
    price = _dig(ctx.fetch(watch).json(), watch.params["path"])
    limit = float(watch.params["max"])
    if isinstance(price, (int, float)) and price <= limit:
        return CheckResult(True, f"Price dropped to {price} (limit {limit:g})")
    return CheckResult(False, f"Price is {price}")
