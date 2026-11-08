#!/usr/bin/env python3
"""Download one Actions artifact zip with N parallel HTTP Range chunks.

Single-stream gh downloads of the 113 MB artifact have been stalling, so fetch
the same bytes over N ranged connections. The GitHub token stays in memory: it
is read from `gh auth token`, sent only in the Authorization header of the
api.github.com request, and never written to disk or to a command line.

		NCHUNK=8 tools/fetch_artifact.py <workflow-run-id> <out.zip>
"""
import fcntl
import http.client
import json
import os
import subprocess
import sys
import threading
import zipfile

REPO = "i7m7r8/flame-mainline"
NCHUNK = int(os.environ.get("NCHUNK", "8"))


def token():
    return subprocess.check_output(["gh", "auth", "token"]).decode().strip()


def api(tok, path):
    c = http.client.HTTPSConnection("api.github.com", timeout=60)
    c.request("GET", path, headers={
        "Authorization": "token " + tok,
        "Accept": "application/vnd.github+json",
        "User-Agent": "flame-harness",
    })
    r = c.getresponse()
    body = r.read()
    c.close()
    if r.status != 200:
        raise RuntimeError("api %s -> %s %s" % (path, r.status, body[:200]))
    return json.loads(body)


def signed_url(tok, archive_path):
    """Follow the 302 by hand: the blob URL needs no auth and takes Range."""
    c = http.client.HTTPSConnection("api.github.com", timeout=60)
    c.request("GET", archive_path, headers={
        "Authorization": "token " + tok,
        "User-Agent": "flame-harness",
    })
    r = c.getresponse()
    loc = r.getheader("Location")
    r.read()
    c.close()
    if r.status not in (301, 302, 303, 307, 308) or not loc:
        raise RuntimeError("expected redirect for %s, got %s" % (archive_path, r.status))
    return loc


def total_size(url):
    u = url.split("/", 3)
    host, path = u[2], "/" + u[3]
    c = http.client.HTTPSConnection(host, timeout=60)
    c.request("GET", path, headers={"Range": "bytes=0-0", "User-Agent": "flame-harness"})
    r = c.getresponse()
    cr = r.getheader("Content-Range")
    r.read()
    c.close()
    if r.status != 206 or not cr:
        raise RuntimeError("probe got %s %r" % (r.status, cr))
    return int(cr.split("/")[1])


def fetch(url, start, end, out, off):
    u = url.split("/", 3)
    host, path = u[2], "/" + u[3]
    c = http.client.HTTPSConnection(host, timeout=120)
    c.request("GET", path, headers={"Range": "bytes=%d-%d" % (start, end),
                                   "User-Agent": "flame-harness"})
    r = c.getresponse()
    if r.status != 206:
        r.read()
        c.close()
        raise RuntimeError("chunk %d-%d got %s" % (start, end, r.status))
    got = 0
    with open(out, "r+b") as f:
        f.seek(off)
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
            got += len(b)
    c.close()
    want = end - start + 1
    if got != want:
        raise RuntimeError("chunk %d-%d short: %d of %d" % (start, end, got, want))
    return got


def take_lock(out):
    """Two downloaders on one output path interleave their writes and produce a
    file with holes that still reports the right length - that is how the first
    corrupt copy of a 113 MB artifact was made. One writer at a time, and the
    lock dies with the process."""
    fd = os.open(out + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("another fetch is writing %s (lock held)" % out)
    return fd


def main():
    run = sys.argv[1]
    out = sys.argv[2]
    lock_fd = take_lock(out)  # noqa: F841 - held until exit
    tok = token()
    arts = api(tok, "/repos/%s/actions/runs/%s/artifacts" % (REPO, run))["artifacts"]
    if not arts:
        raise RuntimeError("run %s has no artifacts" % run)
    a = max(arts, key=lambda x: x["size_in_bytes"])
    print("artifact %s  reported %d bytes  (keys %s)"
          % (a["name"], a["size_in_bytes"], ",".join(sorted(a))))
    archive_path = "/" + a["archive_download_url"].split("api.github.com/", 1)[1]

    # The API's size_in_bytes matches the blob length here, but take the
    # authoritative number from the blob's own Content-Range probe.
    url0 = signed_url(tok, archive_path)
    total = total_size(url0)
    print("blob reports %d bytes" % total)

    bounds = [(i * total // NCHUNK, (i + 1) * total // NCHUNK - 1) for i in range(NCHUNK)]
    if not os.path.exists(out) or os.path.getsize(out) != total:
        with open(out, "wb") as f:
            f.truncate(total)

    # A 113 MB fetch can outlive a shell timeout, so remember which slices
    # already landed and let the next run fill only the rest.
    sidecar = out + ".done"
    state = {"total": total, "nchunk": NCHUNK, "chunks": []}
    if os.path.exists(sidecar):
        try:
            prev = json.load(open(sidecar))
            if prev.get("total") == total and prev.get("nchunk") == NCHUNK:
                state = prev
                print("resuming, %d of %d chunks already in place"
                      % (len(state["chunks"]), NCHUNK))
        except Exception:  # noqa: BLE001
            pass
    lock = threading.Lock()

    done = [0] * NCHUNK
    stop = threading.Event()

    def work(i):
        s, e = bounds[i]
        if i in state["chunks"]:
            done[i] = e - s + 1
            print("chunk %d/%d already done" % (i + 1, NCHUNK), flush=True)
            return
        for attempt in range(5):
            if stop.is_set():
                return
            try:
                url = signed_url(tok, archive_path) if attempt else url0
                fetch(url, s, e, out, s)
                done[i] = e - s + 1
                with lock:
                    state["chunks"].append(i)
                    json.dump(state, open(sidecar, "w"))
                print("chunk %d/%d done (%d bytes)" % (i + 1, NCHUNK, done[i]), flush=True)
                return
            except Exception as ex:  # noqa: BLE001
                print("chunk %d attempt %d failed: %s" % (i + 1, attempt + 1, ex), flush=True)
                if attempt == 4:
                    stop.set()
                    raise

    ts = [threading.Thread(target=work, args=(i,)) for i in range(NCHUNK)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    if stop.is_set():
        raise RuntimeError("a chunk gave up")

    size = os.path.getsize(out)
    zf = zipfile.ZipFile(out)
    bad = zf.testzip()
    print("file %s  %d bytes  zip entries %d  corrupt=%s"
          % (out, size, len(zf.namelist()), bad))
    if size != total or bad is not None:
        raise RuntimeError("artifact did not verify")
    print("names:", ", ".join(zf.namelist()[:12]))


main()
