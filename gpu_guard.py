# -*- coding: utf-8 -*-
"""Minimal auto-guard: ComfyUI (8188) up -> unload swap models + write pause flag;
ComfyUI gone -> remove flag so paused batches resume. No app kills, no suspend,
no self-heal. ASCII log only."""
import http.client
import json
import os
import socket
import time

FLAG = r"E:\AI\_scripts\gpu_pause.flag"
HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "gpu_guard.log")
POLL = 10


def log(msg):
    try:
        with open(LOG, "a", encoding="ascii") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def comfy_up():
    try:
        with socket.create_connection(("127.0.0.1", 8188), timeout=1):
            return True
    except Exception:
        return False


def swap(method, path):
    c = http.client.HTTPConnection("127.0.0.1", 9292, timeout=10)
    try:
        c.request(method, path)
        r = c.getresponse()
        body = r.read()
        return r.status, (json.loads(body) if body else {})
    finally:
        c.close()


def unload():
    try:
        st, _ = swap("POST", "/api/models/unload")
        log("unload HTTP %d" % st)
    except Exception as e:
        log("unload unreachable: %s" % type(e).__name__)


def running_count():
    try:
        st, d = swap("GET", "/running")
        return len(d.get("running") or []) if st == 200 else 0
    except Exception:
        return 0


def main():
    try:
        s = socket.socket()
        s.bind(("127.0.0.1", 9294))
        s.listen(1)
    except OSError:
        return  # another guard instance holds the port
    paused = os.path.exists(FLAG)
    log("guard start paused=%s" % paused)
    while True:
        up = comfy_up()
        if up and not paused:
            unload()
            try:
                open(FLAG, "w").close()
            except Exception as e:
                log("flag write fail: %s" % type(e).__name__)
            paused = True
            log("ComfyUI up -> paused")
        elif up and paused:
            if running_count() > 0:
                unload()
                log("re-unloaded stray model")
        elif not up and paused:
            try:
                os.remove(FLAG)
            except Exception:
                pass
            paused = False
            log("ComfyUI gone -> resumed")
        time.sleep(POLL)


if __name__ == "__main__":
    main()