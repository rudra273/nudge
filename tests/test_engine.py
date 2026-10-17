from nudge.engine import is_due, next_run, run_due, run_watch
from nudge.models import Watch, parse_duration
from nudge.store import Store

URL = "https://example.com/t"


def make(store, **kw):
    return store.add(Watch(id=kw.pop("id", "w1"), name="Tickets", url=URL, check="keyword",
                           params={"text": "Book now"}, **kw))


def test_parse_duration():
    assert parse_duration("90") == 90
    assert parse_duration("15m") == 900
    assert parse_duration("2h") == 7200


def test_schedule_interval_and_cron():
    w = Watch(id="a", name="a", url=URL, interval="10m")
    assert next_run(w, None) == 0
    assert next_run(w, 1000) == 1600
    assert is_due(w, {"last_checked": 1000}, 1600)
    assert not is_due(w, {"last_checked": 1000}, 1500)
    assert is_due(w, {"last_checked": 1000}, 1500, grace=120)
    w.enabled = False
    assert not is_due(w, {}, 0)
    c = Watch(id="b", name="b", url=URL, interval=None, cron="0 * * * *")
    assert next_run(c, 0) == 3600  # epoch 0 is 00:00 UTC; next top of hour


def test_notifies_on_transition_only(store, web):
    w = make(store)
    sent = []
    web.set(URL, "Coming soon")
    assert not run_watch(store, w, sent.append, fetcher=web).notified
    web.set(URL, "Book now!")
    assert run_watch(store, w, sent.append, fetcher=web).notified
    assert not run_watch(store, w, sent.append, fetcher=web).notified  # still matched: no spam
    web.set(URL, "Sold out")
    run_watch(store, w, sent.append, fetcher=web)
    web.set(URL, "Book now")
    assert run_watch(store, w, sent.append, fetcher=web).notified  # re-armed after it went away
    assert len(sent) == 2 and "Tickets" in sent[0] and URL in sent[0]


def test_notify_once_pauses(store, web):
    w = make(store, notify_once=True)
    web.set(URL, "Book now")
    out = run_watch(store, w, None, fetcher=web)
    assert out.notified and not w.enabled and store.dirty


def test_errors_alert_once_at_threshold(store, web):
    w = make(store)
    web.pages[URL] = RuntimeError("blocked")
    sent = []
    for _ in range(5):
        out = run_watch(store, w, sent.append, fetcher=web)
        assert out.error
    assert len(sent) == 1 and "failed 3 checks" in sent[0]
    web.set(URL, "fine")
    run_watch(store, w, sent.append, fetcher=web)
    assert store.state_for(w)["errors"] == 0 and "last_error" not in store.state_for(w)


def test_run_due_respects_schedule_and_persists(tmp_path, web):
    store = Store.load(tmp_path / "w.yaml", tmp_path / "s.json")
    make(store, interval="1h")
    make(store, id="w2", enabled=False)
    web.set(URL, "nothing")
    assert len(run_due(store, None, now=10_000, fetcher=web)) == 1
    assert run_due(store, None, now=10_100, fetcher=web) == []
    store.save()
    again = Store.load(tmp_path / "w.yaml", tmp_path / "s.json")
    assert [w.id for w in again.watches] == ["w1", "w2"]
    assert again.state["watches"]["w1"]["last_checked"] == 10_000
    assert len(run_due(again, None, now=10_000 + 3600, fetcher=web)) == 1
