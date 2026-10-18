from nudge.bot import handle_text, process_updates
from nudge.config import Settings


class FakeTelegram:
    def __init__(self, updates, allowed=("42",)):
        self.updates = updates
        self.allowed = allowed
        self.sent = []

    def get_updates(self, offset, timeout=0):
        return [u for u in self.updates if u["update_id"] >= offset]

    def send(self, text, chat_id=None):
        self.sent.append((chat_id, text))

    def is_allowed(self, chat_id):
        return str(chat_id) in self.allowed


def msg(uid, text, chat=42):
    return {"update_id": uid, "message": {"chat": {"id": chat}, "text": text}}


SETTINGS = Settings()


def test_watch_lifecycle(store):
    reply = handle_text('https://example.com/t says "Book now" every 5m', store, SETTINGS)
    assert "Watching" in reply
    wid = store.watches[0].id
    assert wid in handle_text("/list", store, SETTINGS)
    assert "Paused" in handle_text(f"/pause {wid}", store, SETTINGS) and not store.watches[0].enabled
    assert "Resumed" in handle_text(f"/resume@NudgeBot {wid[:3]}", store, SETTINGS) and store.watches[0].enabled
    assert "Removed" in handle_text(f"/remove {wid}", store, SETTINGS) and store.watches == []


def test_errors_are_friendly(store):
    assert "No watch" in handle_text("/pause zzz", store, SETTINGS)
    assert "link" in handle_text("tell me when tickets drop", store, SETTINGS)
    assert "Usage" in handle_text("/pause", store, SETTINGS)
    assert "Unknown command" in handle_text("/frobnicate", store, SETTINGS)


def test_process_updates_auth_and_offset(store):
    tg = FakeTelegram([msg(10, "/help"), msg(11, "/list", chat=999), {"update_id": 12}])
    assert process_updates(tg, store, SETTINGS) == 1
    assert store.telegram_offset == 13
    assert tg.sent[0][0] == "42" and "Nudge" in tg.sent[0][1]
    assert tg.sent[1][0] == "999" and "private" in tg.sent[1][1]
    assert process_updates(tg, store, SETTINGS) == 0  # already consumed


# -- buttons, drafts, defaults ----------------------------------------------------------

import pytest  # noqa: E402

from nudge import bot  # noqa: E402
from nudge.bot import handle_callback  # noqa: E402
from nudge.models import Watch  # noqa: E402
from nudge.telegram import Message, Telegram  # noqa: E402


def keyword_watch(store, wid="w1"):
    return store.add(Watch(id=wid, name="Tickets", url="https://example.com/t", check="keyword",
                           params={"text": "Book now"}))


def test_list_and_card_buttons(store):
    keyword_watch(store)
    listing = handle_text("/list", store, SETTINGS)
    assert listing.buttons == [[{"text": "👀 Tickets", "callback_data": "card:w1"}]]
    card = handle_callback("card:w1", store, SETTINGS).message
    data = [b.get("callback_data") for row in card.buttons for b in row]
    assert {"chk:w1:c", "pause:w1:c", "every:w1:5m:c", "rm:w1", "list"} <= set(data)


def test_callback_actions(store):
    w = keyword_watch(store)
    r = handle_callback("pause:w1:c", store, SETTINGS)
    assert not w.enabled and "Resume" in str(r.message.buttons)
    handle_callback("resume:w1", store, SETTINGS)
    assert w.enabled
    r = handle_callback("every:w1:1m:c", store, SETTINGS)
    assert w.interval == "1m" and "Every 1m" in r.toast
    store.state_for(w)["unacked"] = {"text": "x", "at": 0}
    r = handle_callback("ack:w1", store, SETTINGS)
    assert "unacked" not in store.state_for(w) and r.buttons is not None and r.message is None
    w2 = store.add(Watch(id="d1", name="Movie", url="https://www.district.in/movies/", check="district",
                         params={"movie": "M", "city": "chennai"}))
    handle_callback("upd:d1:0", store, SETTINGS)
    assert w2.params["updates"] is False
    assert "Yes, remove" in str(handle_callback("rm:w1", store, SETTINGS).message.buttons)
    assert store.find("w1")  # asking isn't removing
    handle_callback("rmy:w1", store, SETTINGS)
    assert [x.id for x in store.watches] == ["d1"]
    assert "No watch" in handle_callback("chk:gone", store, SETTINGS).toast


def test_pause_stops_reminders(store):
    w = keyword_watch(store)
    store.state_for(w)["unacked"] = {"text": "x", "at": 0}
    handle_text("/pause w1", store, SETTINGS)
    assert "unacked" not in store.state_for(w)


def test_missing_city_draft_then_answer(store):
    r = handle_text("Avengers Doomsday on District", store, SETTINGS, chat_id="42")
    assert "Which city" in r and r.buttons[0][0]["callback_data"] == "city:bengaluru"
    assert store.watches == []
    r = handle_text("Chennai", store, SETTINGS, chat_id="42")
    assert "Watching" in r and store.watches[0].params["city"] == "chennai"
    assert bot.prefs(store) == {"city": "chennai"} and "default city" in r
    # the saved city is used from now on, and offered first
    handle_text("Jailer 2 on District", store, SETTINGS, chat_id="42")
    assert store.watches[1].params["city"] == "chennai"


def test_city_button_and_cancel(store):
    handle_text("Avengers Doomsday on District", store, SETTINGS, chat_id="42")
    r = handle_callback("city:mumbai", store, SETTINGS, chat_id="42")
    assert "Watching" in r.message and store.watches[0].params["city"] == "mumbai"
    handle_text("amul whey protein", store, SETTINGS, chat_id="42")
    assert "Cancelled" in handle_callback("draft:cancel", store, SETTINGS, chat_id="42").toast
    assert "expired" in handle_callback("city:pune", store, SETTINGS, chat_id="42").toast


def test_new_request_replaces_a_draft(store):
    handle_text("Avengers Doomsday on District", store, SETTINGS, chat_id="42")
    r = handle_text('https://example.com/t says "Book now"', store, SETTINGS, chat_id="42")
    assert "Watching" in r and store.watches[0].check == "keyword"


def test_default_city_and_pincode_commands(store):
    assert "No default city" in handle_text("/city", store, SETTINGS)
    handle_text("/city Bangalore", store, SETTINGS)
    handle_text("/pincode 560001", store, SETTINGS)
    assert bot.prefs(store) == {"city": "bengaluru", "pincode": "560001"}
    assert "6 digits" in handle_text("/pincode 12", store, SETTINGS)
    handle_text("amul whey protein", store, SETTINGS, chat_id="42")
    assert store.watches[0].params["pincode"] == "560001"


def test_ambiguous_movie_asks_which(store, monkeypatch):
    options = ["https://www.district.in/movies/jailer-movie-tickets-in-chennai-MV1",
               "https://www.district.in/movies/jailer-2-movie-tickets-in-chennai-MV2"]
    monkeypatch.setattr(bot, "_movie_choices", lambda watch, settings: options)
    monkeypatch.setattr(bot, "check_now", lambda *a, **k: type("O", (), {"error": "skip"})())
    r = handle_text("Jailer on District in Chennai", store, SETTINGS, notify=lambda m: None, chat_id="42")
    assert "Which" in r and [row[0]["text"] for row in r.buttons[:2]] == ["Jailer", "Jailer 2"]
    r = handle_callback("pick:1", store, SETTINGS, notify=lambda m: None, chat_id="42")
    w = store.watches[0]
    assert w.url == options[1] and w.params["movie"] == "Jailer 2" and w.name.startswith("Jailer 2")


class FakeSession:
    def __init__(self, responses=None):
        self.posts = []
        self.responses = list(responses or [])

    def post(self, url, json=None, timeout=None):
        self.posts.append((url.rsplit("/", 1)[-1], json))
        status, body = self.responses.pop(0) if self.responses else (200, {"ok": True, "result": {"message_id": 7}})
        return type("R", (), {"status_code": status, "json": lambda self: body})()


def test_telegram_send_buttons_split_and_fallbacks(monkeypatch):
    s = FakeSession()
    tg = Telegram("T", ["42"], session=s)
    assert tg.send(Message("hi", [[{"text": "a", "callback_data": "x"}]])) == 7
    assert s.posts[0][1]["reply_markup"] == {"inline_keyboard": [[{"text": "a", "callback_data": "x"}]]}

    s.posts.clear()
    tg.send(Message("line\n" * 2000, [[{"text": "a", "callback_data": "x"}]]))
    assert len(s.posts) == 3 and all(len(p[1]["text"]) <= 4096 for p in s.posts)
    assert "reply_markup" not in s.posts[0][1] and "reply_markup" in s.posts[-1][1]  # buttons on the last part

    monkeypatch.setattr("nudge.telegram.time.sleep", lambda s: None)
    s = FakeSession([(429, {"ok": False, "parameters": {"retry_after": 1}}),
                     (400, {"ok": False, "description": "Bad Request: can't parse entities"})])
    Telegram("T", ["42"], session=s).send("<b>broken")
    assert len(s.posts) == 3 and s.posts[-1][1]["text"] == "broken" and "parse_mode" not in s.posts[-1][1]


class FakeTelegramWithButtons(FakeTelegram):
    def __init__(self, updates):
        super().__init__(updates)
        self.answered, self.edited = [], []

    def send(self, text, chat_id=None, buttons=None):
        self.sent.append((chat_id, text))

    def answer_callback(self, cid, text=""):
        self.answered.append(text)

    def edit(self, chat_id, message_id, text, buttons=None):
        self.edited.append((chat_id, message_id, text))

    def edit_buttons(self, chat_id, message_id, buttons):
        self.edited.append((chat_id, message_id, buttons))


def test_process_updates_handles_button_taps(store):
    keyword_watch(store)
    tap = {"update_id": 5, "callback_query": {"id": "q", "data": "card:w1",
                                              "message": {"message_id": 9, "chat": {"id": 42}}}}
    stranger = {"update_id": 6, "callback_query": {"id": "q2", "data": "rmy:w1",
                                                   "message": {"message_id": 9, "chat": {"id": 999}}}}
    tg = FakeTelegramWithButtons([tap, stranger])
    assert process_updates(tg, store, SETTINGS) == 1
    assert tg.edited[0][:2] == ("42", 9) and "Tickets" in tg.edited[0][2]
    assert "private" in tg.answered[1] and store.find("w1")  # strangers can't press buttons
