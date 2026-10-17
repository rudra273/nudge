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
    assert "URL" in handle_text("tell me when tickets drop", store, SETTINGS)
    assert "Usage" in handle_text("/pause", store, SETTINGS)
    assert "Unknown command" in handle_text("/frobnicate", store, SETTINGS)


def test_process_updates_auth_and_offset(store):
    tg = FakeTelegram([msg(10, "/help"), msg(11, "/list", chat=999), {"update_id": 12}])
    assert process_updates(tg, store, SETTINGS) == 1
    assert store.telegram_offset == 13
    assert tg.sent[0][0] == "42" and "Nudge" in tg.sent[0][1]
    assert tg.sent[1][0] == "999" and "private" in tg.sent[1][1]
    assert process_updates(tg, store, SETTINGS) == 0  # already consumed
