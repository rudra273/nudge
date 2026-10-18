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


def test_recovery_notice_after_error_alert(store, web):
    w = make(store)
    web.pages[URL] = RuntimeError("blocked")
    sent = []
    for _ in range(3):
        run_watch(store, w, sent.append, fetcher=web)
    web.set(URL, "Coming soon")
    run_watch(store, w, sent.append, fetcher=web)
    run_watch(store, w, sent.append, fetcher=web)
    assert len(sent) == 2 and "working again" in sent[1]  # once, and only after an error alert went out


def test_alert_has_book_and_action_buttons(store, web):
    w = make(store)
    web.set(URL, "Book now")
    sent = []
    run_watch(store, w, sent.append, fetcher=web)
    rows = sent[0].buttons
    assert rows[0] == [{"text": "🔗 Open", "url": URL}]
    assert [b["callback_data"] for b in rows[1]] == ["chk:w1", "pause:w1"]
    assert "unacked" not in store.state_for(w)  # not urgent


def test_urgent_alert_repeats_until_acknowledged(store, web):
    w = store.add(Watch(id="w1", name="Tickets", url=URL, check="keyword", params={"text": "Book now", "urgent": True}))
    web.set(URL, "Book now")
    sent = []
    run_due(store, sent.append, now=1000, fetcher=web)
    assert len(sent) == 1 and sent[0].buttons[1][0]["callback_data"] == "ack:w1"
    run_due(store, sent.append, now=1100, fetcher=web)  # too soon for a reminder
    assert len(sent) == 1
    for i in range(1, 8):
        run_due(store, sent.append, now=1000 + 180 * i, fetcher=web)
    reminders = [m for m in sent if "Reminder" in m]
    assert len(reminders) == 5 and "Reminder 5/5" in reminders[-1]  # capped
    assert "unacked" not in store.state_for(w)


def test_district_with_a_date_is_urgent_by_default():
    from nudge.engine import is_urgent

    assert is_urgent(Watch(id="d", name="d", url=URL, check="district", params={"date": "release"}))
    assert not is_urgent(Watch(id="d", name="d", url=URL, check="district", params={}))
    assert not is_urgent(Watch(id="d", name="d", url=URL, check="district", params={"date": "release",
                                                                                     "urgent": False}))
