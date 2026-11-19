import json
import sqlite3
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from sniper.config import Config
from sniper.dashboard import connect_ro, make_handler, snapshot
from sniper.db import DB
from sniper.engine import Sniper
from tests.test_engine import Curve, cfg, pump_up


@pytest.fixture
def seeded(tmp_path):
    path = str(tmp_path / "s.db")
    s = Sniper(cfg(), DB(path))
    c = Curve()
    s.handle(c.create(), 0)
    t = pump_up(s, c, 0)
    s.tick(t + 6); s.tick(t + 1806); s.tick(t + 1812)     # one closed trade (time limit)
    c2 = Curve("N")
    s.handle(c2.create(), 5000)
    pump_up(s, c2, 5000)                                 # one open (pending) trade
    s.db.conn.execute("UPDATE tokens SET name='<img src=x onerror=alert(1)>' WHERE mint='M'")
    s.db.conn.commit()
    return path


def test_snapshot_shapes(seeded):
    d = snapshot(seeded, "all", Config())
    assert len(d["closed"]) == 1 and len(d["open"]) == 1
    assert d["closed"][0]["exit_reason"] == "time_limit"
    assert d["closed"][0]["name"].startswith("<img")        # raw; page renders with textContent
    assert len(d["equity"]["signal"]) == 1
    assert d["stats"]["signal"]["trades"] == 1


def test_window_filters_old_trades(seeded):
    d = snapshot(seeded, "24h", Config())                   # synthetic trades are from 1970
    assert d["closed"] == [] and d["equity"]["signal"] == []
    assert len(d["open"]) == 1                              # open trades always shown


def test_connection_is_read_only(seeded):
    conn = connect_ro(seeded)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM positions")


@pytest.fixture
def server(seeded, tmp_path):
    def start(db):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(db, Config()))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv
    servers = []
    yield lambda db=seeded: servers.append(start(db)) or f"http://127.0.0.1:{servers[-1].server_port}"
    for s in servers:
        s.shutdown()


def test_http_endpoints(server):
    base = server()
    html = urllib.request.urlopen(base + "/").read().decode()
    assert "<title>Coin Sniper</title>" in html and "innerHTML" not in html
    r = urllib.request.urlopen(base + "/api/data?window=bogus")
    assert r.headers["Content-Type"] == "application/json"
    assert json.loads(r.read())["window"] == "all"
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(base + "/nope")
    assert e.value.code == 404


def test_missing_db_is_503(server, tmp_path):
    base = server(str(tmp_path / "missing.db"))
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(base + "/api/data")
    assert e.value.code == 503
    assert not (tmp_path / "missing.db").exists()            # never creates the file
