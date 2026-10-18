import time

from nudge import cli
from nudge.config import Settings


def test_bounded_run_stops_at_deadline_and_saves(tmp_path, monkeypatch):
    settings = Settings(watches_path=tmp_path / "w.yaml", state_path=tmp_path / "s.json", tick_seconds=1)
    app = cli.App(settings)
    ticks = []
    real_tick = cli.tick
    monkeypatch.setattr(cli, "tick", lambda app, grace=0.0, poll_timeout=0: (ticks.append(grace),
                                                                              real_tick(app, grace, poll_timeout)))
    started = time.time()
    cli._loop(app, grace=60, deadline=started + 2.2)
    assert 2 <= time.time() - started < 4
    assert len(ticks) == 3 and ticks[0] == 0.0 and ticks[-1] == 60  # only the end of the run looks ahead
    assert (tmp_path / "s.json").exists()


def test_run_parser_accepts_listen():
    args = cli.build_parser().parse_args(["run", "--once", "--listen", "8m", "--grace", "2m"])
    assert args.once and args.listen == "8m"
