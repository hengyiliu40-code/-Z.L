# -*- coding: utf-8 -*-
"""
Ferrari F1 Cyber Widget - RACE TIMELINE panel
----------------------------------------------
右下角赛程面板：下一场比赛倒计时 + 后续赛程 + 法拉利车队/车手积分。

设计尺寸严格对齐壁纸右下角 RACE TIMELINE 分区框
（实测物理 406x101 @125% 缩放 => 逻辑 325x81 @ (1191, 727)）。

数据来源：Jolpica-F1（Ergast 官方继任者，免费无 key）
  https://api.jolpi.ca/ergast/f1/<season>/races.json
  https://api.jolpi.ca/ergast/f1/<season>/driverstandings.json
  https://api.jolpi.ca/ergast/f1/<season>/constructorstandings.json

断网时使用本地缓存（cache.json），倒计时照常按缓存赛历推进，绝不伪造数据。
时间一律按系统本地时区显示（当前 = 曼谷 UTC+7）。
"""

import ctypes
import json
import os
import sys
import threading
import time
import traceback
import urllib.request
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
from pathlib import Path

import tkinter as tk

# ----------------------------------------------------------------------------
# 基本参数
# ----------------------------------------------------------------------------
APP_NAME = "Ferrari F1 Race Timeline"
CONFIG_FOLDER = "FerrariF1Timeline"

WIN_W, WIN_H = 325, 81          # 逻辑像素，与壁纸右下角框 1:1 对齐
CUT = 10                        # 八边形切角
FIXED_LEFT, FIXED_TOP = 1191, 727   # 物理 (1489, 909) / 1.25，固定不动

BG = "#07070a"
RED = "#e01b2c"
RED_SOFT = "#ff3b4a"
RED_DARK = "#5c0f18"
WHITE = "#f4f0f2"
MUTED = "#8b7f86"
FAINT = "#2c2024"
CYAN = "#2ee6d6"
GOLD = "#f0c24a"

REFRESH_SECONDS = 1800          # 30 分钟拉一次数据

API = "https://api.jolpi.ca/ergast/f1/{season}/{path}.json"

# 与时钟组件共用同一套"城市时区"：读它的 settings.json 里的 utc_offset，
# 这样两个组件永远显示同一个当地时间（默认 Bangkok，UTC+7）。
CLOCK_SETTINGS = Path(os.environ.get("APPDATA") or Path.home()) / \
    "FerrariF1WidgetCompact" / "settings.json"
DEFAULT_OFFSET = 7 * 3600


def local_tz():
    offset = DEFAULT_OFFSET
    try:
        data = json.loads(CLOCK_SETTINGS.read_text(encoding="utf-8"))
        offset = int(data.get("utc_offset", DEFAULT_OFFSET))
    except Exception:
        pass
    return timezone(timedelta(seconds=offset)), offset

# ----------------------------------------------------------------------------
# Win32
# ----------------------------------------------------------------------------
U32 = ctypes.WinDLL("user32", use_last_error=True)
K32 = ctypes.WinDLL("kernel32", use_last_error=True)
G32 = ctypes.WinDLL("gdi32", use_last_error=True)

U32.GetParent.restype = wintypes.HWND
U32.GetParent.argtypes = [wintypes.HWND]
U32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
U32.SetWindowRgn.restype = ctypes.c_int
U32.GetWindowLongW.restype = ctypes.c_long
U32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
U32.SetWindowLongW.restype = ctypes.c_long
U32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
G32.CreatePolygonRgn.restype = wintypes.HRGN
G32.CreatePolygonRgn.argtypes = [ctypes.POINTER(wintypes.POINT), ctypes.c_int, ctypes.c_int]

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
ALTERNATE = 1


# ----------------------------------------------------------------------------
# 配置 / 日志
# ----------------------------------------------------------------------------
def config_dir():
    base = Path(os.environ.get("APPDATA") or Path.home())
    folder = base / CONFIG_FOLDER
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return folder


def cache_path():
    return config_dir() / "cache.json"


def pid_path():
    return config_dir() / "widget.pid"


LOG_MAX = 256 * 1024            # error.log 超过 256KB 自动清空重记


def log_error(msg):
    try:
        path = config_dir() / "error.log"
        if path.exists() and path.stat().st_size > LOG_MAX:
            path.write_text("", encoding="utf-8")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


# ----------------------------------------------------------------------------
# 数据
# ----------------------------------------------------------------------------
def http_json(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "FerrariRaceTimelineWidget/1.0",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_race_dt(race, tz):
    """Ergast 的 date/time 是 UTC，转成与时钟组件一致的城市当地时间。"""
    date_s = race.get("date")
    time_s = race.get("time") or "13:00:00Z"
    if time_s.endswith("Z"):
        time_s = time_s[:-1]
    dt = datetime.fromisoformat(f"{date_s}T{time_s}")
    return dt.replace(tzinfo=timezone.utc).astimezone(tz)


def slim_race(race):
    """只保留渲染需要的 4 个字段，丢掉 URL / 赛道经纬度 / 练习赛时间等无关数据。"""
    return {
        "round": race.get("round"),
        "name": race.get("raceName") or race.get("name") or "GP",
        "date": race.get("date"),
        "time": race.get("time") or "13:00:00Z",
    }


def short_name(race):
    name = race.get("name") or race.get("raceName") or ""
    suffix = " Grand Prix"
    if name.endswith(suffix):
        name = name[: -len(suffix)]
    return (name.upper() + " GP") if name else "GP"


def fetch_season():
    """拉赛历 + 法拉利积分，返回 dict 或抛异常。"""
    year = datetime.now().year
    data = None
    for season in (year, year - 1):
        try:
            races_raw = http_json(API.format(season=season, path="races")) \
                ["MRData"]["RaceTable"]["Races"]
        except Exception:
            races_raw = []
        if races_raw:
            data = {"v": 2, "season": season,
                    "races": [slim_race(r) for r in races_raw]}
            break
    if not data:
        raise RuntimeError("no race calendar")

    season = data["season"]
    data["team"] = None
    data["drivers"] = []
    try:
        lst = http_json(API.format(season=season, path="constructorstandings")) \
            ["MRData"]["StandingsTable"]["StandingsLists"][0]
        for s in lst.get("ConstructorStandings", []):
            if "ferrari" in s["Constructor"]["name"].lower():
                data["team"] = {"pos": s["position"], "points": s["points"],
                                "name": "FERRARI"}
                data["round"] = lst.get("round")
                break
    except Exception:
        log_error("team standings failed: " + traceback.format_exc())

    try:
        lst = http_json(API.format(season=season, path="driverstandings")) \
            ["MRData"]["StandingsTable"]["StandingsLists"][0]
        for s in lst.get("DriverStandings", []):
            cons = s.get("Constructors") or []
            if cons and "ferrari" in cons[0]["name"].lower():
                data["drivers"].append({
                    "code": s["Driver"].get("code") or s["Driver"]["familyName"][:3].upper(),
                    "pos": s["position"],
                    "points": s["points"],
                })
    except Exception:
        log_error("driver standings failed: " + traceback.format_exc())

    data["ts"] = time.time()
    return data


def load_cache():
    try:
        return json.loads(cache_path().read_text(encoding="utf-8"))
    except Exception:
        return None


def save_cache(data):
    """原子写入：先写临时文件再替换，避免半截文件；单文件覆盖不累积。"""
    try:
        tmp = cache_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                       encoding="utf-8")
        os.replace(tmp, cache_path())
    except Exception:
        log_error("save cache failed: " + traceback.format_exc())


# ----------------------------------------------------------------------------
# 界面
# ----------------------------------------------------------------------------
class TimelinePanel:
    def __init__(self):
        self.data = load_cache()
        self.races = []          # [(start_dt, name, round)]
        self.next_race = None
        self.fresh = False
        self.hwnd = None
        self.tz, self.offset = local_tz()

        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.overrideredirect(True)
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", False)
        self.root.geometry(f"{WIN_W}x{WIN_H}+{FIXED_LEFT}+{FIXED_TOP}")

        self.canvas = tk.Canvas(self.root, width=WIN_W, height=WIN_H, bg=BG,
                                highlightthickness=0, bd=0)
        self.canvas.pack()

        self.build()
        self.rebuild_schedule()
        self.bind_events()
        self.root.after(80, self.apply_window_shape)
        self.root.after(200, self.tick)
        self.root.after(400, self.refresh_async)

    # ---------- 形状 ----------
    def octagon(self, inset=0.0):
        w, h = WIN_W - inset, WIN_H - inset
        o = inset / 2.0
        c = CUT - inset / 2.0
        pts = [(o + c, o), (o + w - c, o), (o + w, o + c), (o + w, o + h - c),
               (o + w - c, o + h), (o + c, o + h), (o, o + h - c), (o, o + c)]
        flat = []
        for px, py in pts:
            flat.extend([px, py])
        return pts, flat

    def apply_window_shape(self):
        try:
            self.hwnd = U32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            _, flat = self.octagon()
            arr = (wintypes.POINT * 8)()
            for i in range(8):
                arr[i].x = int(flat[i * 2])
                arr[i].y = int(flat[i * 2 + 1])
            rgn = G32.CreatePolygonRgn(arr, 8, ALTERNATE)
            if rgn:
                U32.SetWindowRgn(self.hwnd, rgn, True)
            ex = U32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
            U32.SetWindowLongW(self.hwnd, GWL_EXSTYLE,
                               (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
        except Exception:
            log_error("shape failed: " + traceback.format_exc())

    # ---------- 绘制 ----------
    def build(self):
        c = self.canvas
        W = WIN_W

        pts, _ = self.octagon(1.0)
        c.create_polygon(*[v for p in pts for v in p], fill=BG,
                         outline=RED_DARK, width=1)
        _, flat = self.octagon(3.0)
        c.create_polygon(flat, fill="", outline=RED, width=1)

        c.create_text(10, 10, anchor="w", text="///", fill=RED,
                      font=("Segoe UI", 7, "bold"))
        c.create_text(24, 10, anchor="w", text="RACE TIMELINE", fill=WHITE,
                      font=("Segoe UI", 7, "bold"))
        c.create_line(10, 16.5, W - 10, 16.5, fill=FAINT, width=1)

        self.status = c.create_text(W - 34, 10, anchor="e", text="● --",
                                    fill=MUTED, font=("Segoe UI", 5))
        self.close_btn = c.create_text(W - 12, 10, anchor="e", text="✕",
                                       fill=MUTED, font=("Segoe UI", 7, "bold"))

        # 倒计时
        self.count_text = c.create_text(10, 35, anchor="w", text="--D --H --M",
                                        fill=WHITE, font=("Consolas", 14, "bold"))
        self.race_text = c.create_text(10, 46, anchor="w", text="LOADING CALENDAR",
                                       fill=WHITE, font=("Segoe UI", 6))
        self.race_sub = c.create_text(10, 57, anchor="w", text="", fill=RED_SOFT,
                                      font=("Segoe UI", 6))

        c.create_line(176, 21, 176, 60, fill=FAINT, width=1)

        # 法拉利积分
        c.create_text(186, 26, anchor="w", text="SCUDERIA FERRARI", fill=RED,
                      font=("Segoe UI", 6, "bold"))
        self.team_pos = c.create_text(186, 43, anchor="w", text="P-",
                                      fill=WHITE, font=("Segoe UI", 13, "bold"))
        self.team_pts = c.create_text(214, 43, anchor="w", text="---",
                                      fill=WHITE, font=("Segoe UI", 13, "bold"))
        self.team_unit = c.create_text(256, 43, anchor="w", text="PTS",
                                       fill=MUTED, font=("Segoe UI", 5))
        self.driver_text = c.create_text(186, 54, anchor="w", text="",
                                         fill=GOLD, font=("Segoe UI", 6, "bold"))

        # 后续赛程
        c.create_line(10, 62, W - 10, 62, fill=FAINT, width=1)
        self.next_top = 70

    # ---------- 事件 ----------
    def bind_events(self):
        c = self.canvas
        c.tag_bind(self.close_btn, "<Button-1>", self.on_close_click)
        c.tag_bind(self.close_btn, "<Enter>",
                   lambda e: c.itemconfigure(self.close_btn, fill=RED_SOFT))
        c.tag_bind(self.close_btn, "<Leave>",
                   lambda e: c.itemconfigure(self.close_btn, fill=MUTED))
        c.bind("<Button-3>", self.on_menu)
        self.menu = tk.Menu(self.root, tearoff=0, bg="#121216", fg=WHITE,
                            activebackground=RED, activeforeground=WHITE,
                            borderwidth=0, font=("Segoe UI", 9))
        self.menu.add_command(label="立即刷新", command=self.refresh_async)
        self.menu.add_separator()
        self.menu.add_command(label="关闭面板", command=self.close)

    def on_close_click(self, e):
        self.close()
        return "break"

    def on_menu(self, e):
        self.menu.tk_popup(e.x_root, e.y_root)

    # ---------- 数据刷新 ----------
    def refresh_async(self):
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self):
        try:
            data = fetch_season()
            save_cache(data)
            self.fresh = True
            self.data = data
            self.root.after(0, self.rebuild_schedule)
        except Exception:
            log_error("refresh failed: " + traceback.format_exc())
            self.root.after(0, lambda: self.canvas.itemconfigure(
                self.status, text="● OFFLINE", fill=MUTED))
        finally:
            if self.fresh:
                self.root.after(0, lambda: self.canvas.itemconfigure(
                    self.status, text="● SYNC", fill=CYAN))
            self.root.after(REFRESH_SECONDS * 1000, self.refresh_async)

    def render_next(self, pairs):
        """底部赛程条：逐段着色，按实际文字宽度依次排布。"""
        c = self.canvas
        c.delete("nxt")
        x = 10
        for text, color, bold in pairs:
            font = ("Segoe UI", 6, "bold") if bold else ("Segoe UI", 6)
            item = c.create_text(x, self.next_top, anchor="w", text=text,
                                 fill=color, font=font, tags="nxt")
            bb = c.bbox(item)
            x = (bb[2] if bb else x + 40) + 5

    def rebuild_schedule(self):
        c = self.canvas
        self.tz, self.offset = local_tz()
        races = (self.data or {}).get("races") or []
        self.races = []
        for r in races:
            try:
                self.races.append((parse_race_dt(r, self.tz), short_name(r), r.get("round")))
            except Exception:
                continue
        self.races.sort(key=lambda x: x[0])

        now = datetime.now(self.tz)
        upcoming = [x for x in self.races if x[0] > now]
        self.next_race = upcoming[0] if upcoming else None
        hours = self.offset // 3600
        tz_label = "UTC+%d" % hours if hours >= 0 else "UTC-%d" % abs(hours)

        if self.next_race:
            dt, name, rnd = self.next_race
            c.itemconfigure(self.race_text, text=f"{name} · R{rnd}")
            c.itemconfigure(self.race_sub,
                            text=f"{dt.strftime('%d %b %H:%M').upper()} · {tz_label}")
            nxt = upcoming[1:3]
            segs = [("UP NEXT", MUTED, True)]
            for i, (d, n, _) in enumerate(nxt):
                if i:
                    segs.append(("·", FAINT, False))
                segs.append((d.strftime("%d %b").upper(), RED_SOFT, True))
                segs.append((n, "#c9bcc2", False))
            if not nxt:
                segs.append(("SEASON FINALE", MUTED, False))
            self.render_next(segs)
        else:
            c.itemconfigure(self.count_text, text="SEASON OVER")
            c.itemconfigure(self.race_text, text="CALENDAR COMPLETE")
            c.itemconfigure(self.race_sub, text="")
            c.delete("nxt")

        team = (self.data or {}).get("team")
        if team:
            c.itemconfigure(self.team_pos, text=f"P{team['pos']}")
            c.itemconfigure(self.team_pts, text=f"{team['points']}")
        else:
            c.itemconfigure(self.team_pos, text="P-")
            c.itemconfigure(self.team_pts, text="---")

        drivers = (self.data or {}).get("drivers") or []
        if drivers:
            parts = [f"{d['code']} {d['points']} P{d['pos']}" for d in drivers]
            c.itemconfigure(self.driver_text, text=" · ".join(parts))
        else:
            c.itemconfigure(self.driver_text, text="")

        self.update_countdown()

    def update_countdown(self):
        if not self.next_race:
            return
        dt = self.next_race[0]
        now = datetime.now(self.tz)
        delta = dt - now
        secs = int(delta.total_seconds())

        if -3 * 3600 < secs <= 0:
            text, color = "RACE LIVE", RED_SOFT
        elif secs <= 0:
            text, color = "FINISHED", MUTED
        else:
            d, rem = divmod(secs, 86400)
            h, rem = divmod(rem, 3600)
            m, s = divmod(rem, 60)
            if d > 0:
                text = f"{d}D {h:02d}H {m:02d}M"
            elif h > 0:
                text = f"{h:02d}H {m:02d}M {s:02d}S"
            else:
                text = f"{m:02d}M {s:02d}S"
            color = WHITE

        self.canvas.itemconfigure(self.count_text, text=text, fill=color)

    # ---------- 主循环 ----------
    def tick(self):
        try:
            self.update_countdown()
        except Exception:
            log_error("tick failed: " + traceback.format_exc())
        self.root.after(1000, self.tick)

    def close(self):
        try:
            pid_path().unlink()
        except Exception:
            pass
        self.root.destroy()

    def run(self):
        try:
            pid_path().write_text(str(os.getpid()), encoding="utf-8")
        except Exception:
            pass
        self.root.mainloop()


def single_instance():
    K32.CreateMutexW.restype = wintypes.HANDLE
    K32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    handle = K32.CreateMutexW(None, False, "Global\\FerrariF1RaceTimeline")
    if not handle:
        return True
    globals()["_mutex_handle"] = handle
    return ctypes.get_last_error() != 183


def main():
    if not single_instance():
        sys.exit(0)
    try:
        app = TimelinePanel()
        app.run()
    except Exception:
        log_error("fatal: " + traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
