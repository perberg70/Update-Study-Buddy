"""Download retry, timeout, truncation and cleanup.

Serves real HTTP from a local socket, so urlopen, the timeout and the retry
loop are genuinely exercised rather than stubbed.

Run: python tests/test_download.py
"""

import http.server
import os
import socketserver
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from organize_content import download_with_retry, unique_name, extract_audio  # noqa: E402

PAYLOAD = b"x" * 5000
STATE = {"fail_first": 0, "hits": 0}


class QuietServer(socketserver.TCPServer):
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        pass  # a disconnecting client is expected in the timeout test


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def do_GET(self):
        STATE["hits"] += 1
        if self.path == "/flaky":
            if STATE["hits"] <= STATE["fail_first"]:
                self.send_response(500)
                self.end_headers()
                return
            body = PAYLOAD
        elif self.path == "/truncated":
            self.send_response(200)
            self.send_header("Content-Length", str(len(PAYLOAD) * 2))  # lies
            self.end_headers()
            self.wfile.write(PAYLOAD)
            return
        elif self.path == "/slow":
            time.sleep(5)
            body = PAYLOAD
        else:
            body = PAYLOAD
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def check(failures, cond, msg):
    if not cond:
        failures.append(msg)


def main():
    failures = []
    with QuietServer(("127.0.0.1", 0), Handler) as httpd:
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{port}"

        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "ok.mp4")
            written = download_with_retry(f"{base}/ok", dest, timeout=5, retries=2)
            check(failures, written == len(PAYLOAD), f"wrote {written}, expected {len(PAYLOAD)}")
            check(failures, os.path.exists(dest), "successful download missing")
            check(failures, not os.path.exists(dest + ".part"), ".part not cleaned up on success")

            # Fails twice, succeeds on the third attempt.
            STATE.update(fail_first=2, hits=0)
            dest2 = os.path.join(tmp, "flaky.mp4")
            t0 = time.time()
            download_with_retry(f"{base}/flaky", dest2, timeout=5, retries=3)
            check(failures, os.path.exists(dest2), "retry should eventually succeed")
            check(failures, time.time() - t0 >= 6, "backoff should wait 2s then 4s")

            # Exhausts retries.
            STATE.update(fail_first=99, hits=0)
            dest3 = os.path.join(tmp, "dead.mp4")
            try:
                download_with_retry(f"{base}/flaky", dest3, timeout=5, retries=2)
                failures.append("a permanently failing download should raise")
            except IOError:
                pass
            check(failures, not os.path.exists(dest3), "no file should remain after failure")
            check(failures, not os.path.exists(dest3 + ".part"), ".part left behind on failure")

            # Server claims more bytes than it sends.
            dest4 = os.path.join(tmp, "trunc.mp4")
            try:
                download_with_retry(f"{base}/truncated", dest4, timeout=5, retries=1)
                failures.append("a truncated response must not be accepted")
            except IOError:
                pass
            check(failures, not os.path.exists(dest4), "truncated file should not be kept")

            # A stalled server must time out rather than hang.
            dest5 = os.path.join(tmp, "slow.mp4")
            t0 = time.time()
            try:
                download_with_retry(f"{base}/slow", dest5, timeout=1, retries=1)
                failures.append("a stalled response should time out")
            except IOError:
                pass
            check(failures, time.time() - t0 < 4, "timeout was not honoured")

        httpd.shutdown()

    taken = set()
    names = [unique_name("Video.mp3", taken) for _ in range(3)]
    check(failures, names == ["Video.mp3", "Video_2.mp3", "Video_3.mp3"],
          f"collisions should disambiguate, got {names}")

    if os.path.exists("/usr/bin/ffmpeg"):
        try:
            extract_audio("/nonexistent.mp4", "/tmp/out.mp3")
            failures.append("ffmpeg failure should raise")
        except RuntimeError as exc:
            check(failures, "ffmpeg exited" in str(exc), "ffmpeg error text not surfaced")
    else:
        print("  [skip] ffmpeg not installed here; error surfacing untested")

    for msg in failures:
        print(f"  [FAIL] {msg}")
    if not failures:
        print("  [PASS] success, retry with backoff, exhausted retries")
        print("  [PASS] truncated response rejected, timeout honoured")
        print("  [PASS] no .part or partial file left behind; name collisions handled")
    return not failures


if __name__ == "__main__":
    print("download hardening")
    raise SystemExit(0 if main() else 1)
