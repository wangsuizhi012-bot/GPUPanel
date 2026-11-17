# -*- coding: utf-8 -*-
"""GPU 清场面板：一键清掉所有 AI 后台进程给 ComfyUI 让路，一键恢复。
配置 gpu_panel.json: kill_patterns(命令行子串) / restore_cmds(恢复时拉起的命令)。
stdout 保持 ASCII（pythonw 下无 stdout 也不崩）。
"""
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox

import psutil

HERE = os.path.dirname(os.path.abspath(__file__))
STACK_DIR = r"E:\AI\_scripts"
CFG_PATH = os.path.join(HERE, "gpu_panel.json")
SWAP = "http://127.0.0.1:9292"
SELF = os.getpid()
NO_WINDOW = 0x08000000

DEFAULTS = {
    "kill_ports": [8188, 9292, 1235, 1236, 1237, 5678, 3080, 8787, 8790],
    "kill_names": ["llama-swap.exe", "llama-server.exe"],
    "kill_patterns": ["ImageAutoRenamer", "agent_ctl.py"],
    "keep_apps": [],
    "app_grace_s": 8,
    "restore_cmds": [],
}


def load_cfg():
    cfg = dict(DEFAULTS)
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def http_json(url, method="GET", timeout=5):
    import http.client
    from urllib.parse import urlparse
    u = urlparse(url)
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=timeout)
    try:
        conn.request(method, u.path or "/")
        r = conn.getresponse()
        body = r.read()
        return r.status, json.loads(body) if body else {}
    finally:
        conn.close()


def vram_used():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8,
            creationflags=NO_WINDOW).stdout.strip()
        used, total = [x.strip() for x in out.split(",")]
        return int(used), int(total)
    except Exception:
        return None, None


def port_up(port):
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except Exception:
        return False


def gateway_up():
    try:
        st, _ = http_json(SWAP + "/v1/models", timeout=3)
        return st == 200
    except Exception:
        return False


def kill_targets(cfg):
    """全清名单：端口监听者 + 进程名 + 命令行子串，连同子进程。只排除面板自身。"""
    ports = set(cfg.get("kill_ports") or [])
    names = {n.lower() for n in cfg.get("kill_names") or []}
    pats = [p.lower() for p in cfg.get("kill_patterns") or []]
    listen = {}
    try:
        for c in psutil.net_connections(kind="tcp"):
            if c.status == psutil.CONN_LISTEN and c.pid and c.laddr and c.laddr.port in ports:
                listen.setdefault(c.pid, []).append(c.laddr.port)
    except Exception:
        pass
    found = {}
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            pid = p.info["pid"]
            if pid == SELF or pid == 0:
                continue
            cmd = " ".join(p.info["cmdline"] or [])
            if "gpu_panel" in cmd.lower():
                continue
            nm = (p.info["name"] or "").lower()
            if pid in listen:
                why = "端口 " + ",".join(str(x) for x in sorted(listen[pid]))
            elif nm in names:
                why = "进程名"
            elif cmd and any(pat in cmd.lower() for pat in pats):
                why = "命令行"
            else:
                continue
            found[pid] = [pid, p.info["name"] or "?", cmd, why]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    for pid in list(found):
        try:
            root = psutil.Process(pid)
            rct = root.create_time()
            for ch in root.children(recursive=True):
                if ch.pid == SELF or ch.pid in found:
                    continue
                try:
                    # PID 复用保险：比"父进程"还老的进程不是它的孩子
                    if ch.create_time() < rct - 1:
                        continue
                    ccmd = " ".join(ch.cmdline() or [])
                except Exception:
                    continue
                found[ch.pid] = [ch.pid, ch.name(), ccmd, "子进程"]
        except Exception:
            continue
    return sorted(found.values())


# 桌面/系统外壳/驱动伴侣/本会话宿主：关了会丢桌面或断会话，永远豁免
APP_EXCLUDE = {
    "explorer.exe", "systemsettings.exe", "applicationframehost.exe",
    "textinputhost.exe", "searchhost.exe", "searchapp.exe",
    "startmenuexperiencehost.exe", "shellexperiencehost.exe",
    "securityhealthsystray.exe", "sihost.exe", "taskhostw.exe",
    "runtimebroker.exe", "dllhost.exe", "ctfmon.exe", "audiodg.exe",
    "nvcontainer.exe", "nvdisplay.container.exe", "nvidia overlay.exe",
    "cua-driver.exe", "conhost.exe",
}
SNAP_PATH = os.path.join(HERE, "gpu_panel_last_closed.json")
WM_CLOSE = 0x0010


def app_windows():
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    # 不声明 argtypes 时 ctypes 按 32 位 int 传句柄，64 位下会截断关错窗口
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    out = []
    CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, lp):
        if u.IsWindowVisible(hwnd):
            n = u.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(hwnd, buf, n + 1)
                pid = wintypes.DWORD()
                u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                out.append((pid.value, hwnd, buf.value))
        return True

    u.EnumWindows(CB(cb), 0)
    return out


def app_targets(cfg):
    """带可见窗口的应用进程（关机时会关的那一类），含窗口句柄供发关闭请求。"""
    keep = {k.lower() for k in cfg.get("keep_apps") or []}
    apps = {}
    for pid, hwnd, title in app_windows():
        if pid == SELF:
            continue
        try:
            p = psutil.Process(pid)
        except Exception:
            continue
        nm = (p.name() or "").lower()
        if nm in APP_EXCLUDE or nm in keep or nm.startswith("qoder"):
            continue
        try:
            cmd = " ".join(p.cmdline() or [])
        except Exception:
            cmd = ""
        if "gpu_panel" in cmd.lower():
            continue
        a = apps.setdefault(pid, {"pid": pid, "name": p.name(), "title": title,
                                  "hwnds": [], "cmd": cmd})
        a["hwnds"].append(hwnd)
    return sorted(apps.values(), key=lambda a: a["pid"])


class Panel:
    def __init__(self, root):
        self.root = root
        self.busy = False
        root.title("GPU 清场面板")
        root.geometry("560x430")
        root.attributes("-topmost", True)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.status = tk.Label(root, text="检测中…", font=("Microsoft YaHei", 11),
                               anchor="w", justify="left")
        self.status.pack(fill="x", padx=12, pady=(12, 6))

        bf = tk.Frame(root)
        bf.pack(fill="x", padx=12)
        self.btn_clear = tk.Button(
            bf, text="一键清场\n（杀掉全部 AI 后台，显存让给 ComfyUI）",
            font=("Microsoft YaHei", 12, "bold"), bg="#c0392b", fg="white",
            height=3, command=self.on_clear)
        self.btn_clear.pack(side="left", expand=True, fill="both", padx=(0, 6))
        self.btn_restore = tk.Button(
            bf, text="恢复后台\n（重新拉起网关和脚本）",
            font=("Microsoft YaHei", 12, "bold"), bg="#27ae60", fg="white",
            height=3, command=self.on_restore)
        self.btn_restore.pack(side="left", expand=True, fill="both")

        tk.Label(root, text="操作记录：", anchor="w").pack(fill="x", padx=12, pady=(10, 0))
        self.log = tk.Text(root, height=10, font=("Consolas", 9), state="disabled")
        self.log.pack(fill="both", expand=True, padx=12, pady=(2, 12))

        self.say("面板就绪。清场前会先列出要杀的进程让你确认。")
        self.refresh_status()

    def say(self, msg):
        line = time.strftime("[%H:%M:%S] ") + msg
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        try:
            with open(os.path.join(HERE, "gpu_panel.log"), "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d ") + line + "\n")
        except Exception:
            pass

    def refresh_status(self):
        if not self.busy:
            used, total = vram_used()
            v = ("%d / %d MiB" % (used, total)) if used is not None else "n/a"
            gw = "运行中" if gateway_up() else "已停止"
            comfy = "运行中" if port_up(8188) else "未启动"
            self.status.configure(
                text="显存 %s    模型网关 %s    ComfyUI %s" % (v, gw, comfy))
        self.root.after(4000, self.refresh_status)

    def set_busy(self, b):
        self.busy = b
        state = "disabled" if b else "normal"
        self.btn_clear.configure(state=state)
        self.btn_restore.configure(state=state)

    def on_close(self):
        if self.busy:
            messagebox.showinfo("请稍候", "正在清场/恢复，完成后才能关闭面板。", parent=self.root)
            return
        self.root.destroy()

    def on_clear(self):
        cfg = load_cfg()
        targets = kill_targets(cfg)
        apps = app_targets(cfg)
        app_pids = {a["pid"] for a in apps}
        stack = [t for t in targets if t[0] not in app_pids]
        roots = [t for t in stack if t[3] != "子进程"]
        nchild = len(stack) - len(roots)
        for a in apps:
            self.say("清单-应用 PID %-6d %-24s %s" % (a["pid"], a["name"], a["title"][:60]))
        for pid, name, cmd, why in stack:
            self.say("清单-后台 PID %-6d %-16s [%s] %s" % (pid, name, why, cmd[:90]))
        lines = ["【应用窗口】"]
        lines += ["  %-24s %s" % (a["name"], a["title"][:50]) for a in apps]
        lines += ["【AI 后台】"]
        lines += ["  PID %-7d %-16s [%s]" % (pid, name, why) for pid, name, cmd, why in roots]
        if nchild:
            lines.append("  …以及 %d 个子进程（明细见面板记录）" % nchild)
        detail = "\n".join(lines) if (apps or roots) else "（当前没有可清的进程）"
        ok = messagebox.askyesno(
            "确认全清（含应用）",
            "与关机相同：先向应用发关闭请求，%d 秒后仍活着的强制结束。\n"
            "未保存的内容会丢失！浏览器标签等恢复后由应用自己决定是否还原。\n"
            "豁免：桌面/设置/输入法/Qoder/本面板。\n\n%s\n\n确定全清？"
            % (cfg.get("app_grace_s", 8), detail), parent=self.root)
        if not ok:
            return
        self.set_busy(True)
        threading.Thread(target=self._clear,
                         args=(targets, apps, cfg.get("app_grace_s", 8)),
                         daemon=True).start()

    def _clear(self, targets, apps, grace):
        import ctypes
        try:
            self.say("开始全清（应用 %d 个 + 后台 %d 个）…" % (len(apps), len(targets)))
            try:
                st, _ = http_json(SWAP + "/api/models/unload", method="POST", timeout=20)
                self.say("卸载全部模型: HTTP %d" % st)
            except Exception as e:
                self.say("卸载接口不可达（网关可能已停）: %s" % e)
            snap = []
            for a in apps:
                try:
                    p = psutil.Process(a["pid"])
                    exe = p.exe()
                    cmdl = p.cmdline()
                except Exception:
                    exe, cmdl = "", []
                snap.append({"pid": a["pid"], "name": a["name"], "title": a["title"],
                             "exe": exe, "cmd": cmdl})
            try:
                with open(SNAP_PATH, "w", encoding="utf-8") as f:
                    json.dump(snap, f, ensure_ascii=False, indent=1)
            except Exception as e:
                self.say("快照写入失败（恢复应用将不可用）: %s" % e)
            u = ctypes.windll.user32
            for a in apps:
                for hwnd in a["hwnds"]:
                    u.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            self.say("已向 %d 个应用发送关闭请求（%d 秒宽限）" % (len(apps), grace))
            time.sleep(1)
            killed = denied = 0
            for pid, name, cmd, why in targets:
                try:
                    p = psutil.Process(pid)
                    p.terminate()
                    try:
                        p.wait(timeout=5)
                    except psutil.TimeoutExpired:
                        p.kill()
                    killed += 1
                    self.say("已结束 PID %d %s [%s]" % (pid, name, why))
                except psutil.NoSuchProcess:
                    continue
                except psutil.AccessDenied:
                    denied += 1
                    self.say("无权限结束 PID %d %s（需管理员运行本面板）" % (pid, name))
            deadline = time.time() + grace
            while time.time() < deadline:
                if not any(psutil.pid_exists(a["pid"]) for a in apps):
                    break
                time.sleep(1)
            forced = 0
            for a in apps:
                try:
                    p = psutil.Process(a["pid"])
                    p.terminate()
                    try:
                        p.wait(timeout=3)
                    except psutil.TimeoutExpired:
                        p.kill()
                    forced += 1
                    self.say("宽限后强制结束 %s (%s)" % (a["name"], a["title"][:30]))
                except psutil.NoSuchProcess:
                    continue
                except psutil.AccessDenied:
                    self.say("无权限结束应用 %s（需管理员）" % a["name"])
            for _ in range(15):
                used, _t = vram_used()
                if used is not None and used < 500:
                    break
                time.sleep(1)
            used, total = vram_used()
            self.say("全清完成：后台 %d 个%s，应用强制 %d 个，显存 %s" % (
                killed, ("，%d 个无权限" % denied) if denied else "", forced,
                ("%d / %d MiB" % (used, total)) if used is not None else "n/a"))
            self.say("现在可以启动 ComfyUI 了。用完点「恢复后台」拉回栈和应用。")
        finally:
            self.root.after(0, lambda: self.set_busy(False))

    def on_restore(self):
        self.set_busy(True)
        threading.Thread(target=self._restore, daemon=True).start()

    def _restore(self):
        try:
            self.say("恢复后台…")
            if gateway_up() and port_up(5678) and port_up(3080):
                self.say("主栈已在运行，跳过 start-stack")
            else:
                subprocess.Popen(["cmd", "/c", os.path.join(STACK_DIR, "start-stack.bat")],
                                 cwd=STACK_DIR, creationflags=NO_WINDOW | subprocess.DETACHED_PROCESS)
                self.say("已拉起 start-stack.bat（n8n + 网关 + DSH）")
            if port_up(1236):
                self.say("嵌入服务(:1236)已在运行")
            else:
                subprocess.Popen(["cmd", "/c", os.path.join(STACK_DIR, "start-embedding-server.bat")],
                                 cwd=STACK_DIR, creationflags=NO_WINDOW | subprocess.DETACHED_PROCESS)
                self.say("已拉起嵌入服务(:1236)")
            try:
                py = sys.executable.replace("pythonw.exe", "python.exe")
                subprocess.Popen([py, r"E:\AI\agent-hub\agent_ctl.py", "ensure"],
                                 cwd=r"E:\AI\agent-hub",
                                 creationflags=NO_WINDOW | subprocess.DETACHED_PROCESS)
                self.say("已调用 agent-hub ensure")
            except Exception as e:
                self.say("agent-hub ensure 失败: %s" % e)
            cfg = load_cfg()
            for item in cfg.get("restore_cmds") or []:
                cmd = item.get("cmd") if isinstance(item, dict) else item
                label = item.get("name", cmd) if isinstance(item, dict) else cmd
                if not cmd:
                    continue
                try:
                    subprocess.Popen(cmd, cwd=item.get("cwd") if isinstance(item, dict) else None,
                                     creationflags=NO_WINDOW | subprocess.DETACHED_PROCESS)
                    self.say("已拉起: %s" % label)
                except Exception as e:
                    self.say("拉起失败 %s: %s" % (label, e))
            try:
                with open(SNAP_PATH, "r", encoding="utf-8") as f:
                    snap = json.load(f)
            except Exception:
                snap = []
            if snap:
                running = set()
                for p in psutil.process_iter(["name"]):
                    try:
                        running.add((p.info["name"] or "").lower())
                    except Exception:
                        continue
                relaunched = 0
                for e in snap:
                    nm = (e.get("name") or "").lower()
                    if nm in running:
                        self.say("应用已在运行，跳过: %s" % e.get("name"))
                        continue
                    cmdl = e.get("cmd") or ([e["exe"]] if e.get("exe") else [])
                    if not cmdl:
                        self.say("无启动信息，跳过: %s" % e.get("name"))
                        continue
                    try:
                        subprocess.Popen(cmdl, creationflags=NO_WINDOW | subprocess.DETACHED_PROCESS)
                        running.add(nm)
                        relaunched += 1
                        self.say("已恢复应用: %s" % e.get("name"))
                    except Exception as ex:
                        self.say("恢复应用失败 %s: %s" % (e.get("name"), ex))
                self.say("应用恢复 %d 个（标签/会话是否还原由应用自身决定）" % relaunched)
            self.say("恢复完成。ComfyUI 由你自己启动；视觉模型下次请求自动加载（首次约 8-40 秒）。")
        finally:
            self.root.after(0, lambda: self.set_busy(False))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    root = tk.Tk()
    Panel(root)
    root.mainloop()


if __name__ == "__main__":
    main()