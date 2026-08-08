"""M1 exit criteria: fetch behavior against a local mock server —
politeness delays, backoff, conditional GETs, cache persistence."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from oem_radar.core.fetch import FetchError, HttpFetcher


class Handler(BaseHTTPRequestHandler):
    flaky_count = 0

    def log_message(self, *a):  # silence
        pass

    def do_GET(self):
        if self.path == "/ok":
            if self.headers.get("If-None-Match") == '"v1"':
                self.send_response(304)
                self.end_headers()
                return
            body = b"hello world"
            self.send_response(200)
            self.send_header("ETag", '"v1"')
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/flaky":
            Handler.flaky_count += 1
            if Handler.flaky_count <= 2:
                self.send_response(500)
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"recovered")
        elif self.path == "/gone":
            self.send_response(404)
            self.end_headers()
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"x")


@pytest.fixture()
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    Handler.flaky_count = 0
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def make_fetcher(tmp_path, **kw):
    defaults = dict(cache_dir=tmp_path / "cache", delay_range=(0.0, 0.0), max_retries=3)
    defaults.update(kw)
    return HttpFetcher(**defaults)


def test_basic_get(server, tmp_path):
    doc = make_fetcher(tmp_path).get(f"{server}/ok")
    assert doc.status == 200 and doc.body == "hello world" and not doc.from_cache


def test_conditional_get_uses_cache(server, tmp_path):
    f = make_fetcher(tmp_path)
    assert f.get(f"{server}/ok").from_cache is False
    doc2 = f.get(f"{server}/ok")  # server replies 304
    assert doc2.from_cache is True and doc2.body == "hello world"
    assert f.stats["cache_hits_304"] == 1
    # cache survives process restarts (new fetcher, same dir)
    f2 = make_fetcher(tmp_path)
    assert f2.get(f"{server}/ok").from_cache is True


def test_backoff_then_success(server, tmp_path):
    sleeps: list[float] = []
    f = make_fetcher(tmp_path, sleep=sleeps.append, backoff_base=2.0)
    doc = f.get(f"{server}/flaky")
    assert doc.body == "recovered"
    assert f.stats["retries"] == 2
    backoffs = [s for s in sleeps if s > 0]
    assert len(backoffs) >= 2 and backoffs[1] > backoffs[0] * 0.6  # growing (jittered)


def test_exhausted_retries_raises(server, tmp_path):
    Handler.flaky_count = -100  # stays 500 for all attempts
    with pytest.raises(FetchError, match="exhausted"):
        make_fetcher(tmp_path, sleep=lambda s: None, max_retries=2).get(f"{server}/flaky")


def test_hard_404_raises_without_retry(server, tmp_path):
    f = make_fetcher(tmp_path)
    with pytest.raises(FetchError) as ei:
        f.get(f"{server}/gone")
    assert ei.value.status == 404
    assert f.stats["requests"] == 1  # no pointless retries on 4xx


def test_politeness_delay_between_same_domain_requests(server, tmp_path):
    sleeps: list[float] = []
    f = make_fetcher(tmp_path, sleep=sleeps.append, delay_range=(0.5, 0.5))
    f.get(f"{server}/a")
    f.get(f"{server}/b")
    assert any(0.3 < s <= 0.5 for s in sleeps)  # waited before the second hit
