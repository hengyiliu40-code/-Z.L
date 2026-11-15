# -*- coding: utf-8 -*-
"""
Ferrari F1 Cyber Clock & Weather Widget - Desktop Jewel Edition
Windows 10/11 | Python 3.9+ | Standard library only
Weather: Open-Meteo (no API key required)

设计目标：
  · 尺寸精确匹配壁纸右上角 "SYSTEM INFO AREA" 分区框（210x243 逻辑像素）
  · 八边形切角处透明，不再是黑方块，像宝石一样嵌进框里
  · 固定位置，不可拖拽
  · 不出现在任务栏 / Alt+Tab，按"显示桌面"也不会消失
  · 开机静默自启（Startup 文件夹里的 VBS）

关闭方式：双击 关闭组件.bat
"""
import ctypes
import json
import os
import sys
import threading
import urllib.parse
import urllib.request
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tkinter as tk
from tkinter import simpledialog

APP_NAME = "Ferrari F1 Cyber Widget"
DEFAULT_CITY = "Bangkok"
DEFAULT_UTC_OFFSET = 7 * 3600      # 默认按曼谷（UTC+7），天气加载后自动跟随所选城市
CONFIG_FOLDER = "FerrariF1WidgetCompact"

# 尺寸与定位：与壁纸 "SYSTEM INFO AREA" 框精确对齐
# 壁纸框实测（125% 缩放下物理像素）：x 1632..1894, y 97..398  =>  262 x 301
# 换算成逻辑像素：210 x 241，左上角 (1306, 78)
WIN_W, WIN_H = 210, 241
CUT = 14                           # 八边形切角
RIGHT_MARGIN = 20                  # 距屏幕右边缘
TOP_MARGIN = 78                    # 距屏幕顶边
DESKTOP_MODE = True                # True = 桌面级窗口（无任务栏、不进 Alt+Tab）

BG = "#09090d"                     # 该颜色会被设为透明（切角处）
PANEL = "#0b0b10"
RED = "#ff2438"
RED_DARK = "#8e1020"
WHITE = "#f4f2f3"
MUTED = "#9a9299"
CYAN = "#69eaff"

U32 = ctypes.WinDLL("user32", use_last_error=True)
K32 = ctypes.WinDLL("kernel32", use_last_error=True)
G32 = ctypes.WinDLL("gdi32", use_last_error=True)

U32.FindWindowW.restype = wintypes.HWND
U32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
U32.GetParent.restype = wintypes.HWND
U32.GetParent.argtypes = [wintypes.HWND]
U32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int, ctypes.c_uint]
U32.IsIconic.restype = wintypes.BOOL
U32.IsIconic.argtypes = [wintypes.HWND]
U32.ShowWindow.restype = wintypes.BOOL
U32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
G32.CreatePolygonRgn.restype = wintypes.HRGN
G32.CreatePolygonRgn.argtypes = [ctypes.POINTER(wintypes.POINT), ctypes.c_int, ctypes.c_int]
U32.SetWindowRgn.restype = ctypes.c_int
U32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]

GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
HWND_NOTOPMOST = -2
SW_SHOWNOACTIVATE = 4


def log_error(msg):
    try:
        p = config_path().parent / "error.log"
        if p.exists() and p.stat().st_size > 256 * 1024:
            p.write_text("", encoding="utf-8")
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass


def single_instance():
    h = K32.CreateMutexW(None, False, "FerrariF1CyberWidgetCompact")
    if not h:
        return True
    return K32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def _get_long(hwnd, index):
    try:
        fn = U32.GetWindowLongPtrW
        fn.restype = ctypes.c_longlong
        fn.argtypes = [wintypes.HWND, ctypes.c_int]
        return fn(hwnd, index)
    except AttributeError:
        U32.GetWindowLongW.restype = ctypes.c_long
        return U32.GetWindowLongW(hwnd, index)


def _set_long(hwnd, index, value):
    try:
        fn = U32.SetWindowLongPtrW
        fn.restype = ctypes.c_longlong
        fn.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_longlong]
        fn(hwnd, index, value)
    except AttributeError:
        U32.SetWindowLongW(hwnd, index, value)


def config_path():
    base = Path(os.environ.get("APPDATA", str(Path.home())))
    folder = base / CONFIG_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "settings.json"


def pid_path():
    return config_path().parent / "widget.pid"


def write_pid():
    try:
        pid_path().write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass


def clear_pid():
    try:
        p = pid_path()
        if p.exists() and p.read_text(encoding="utf-8").strip() == str(os.getpid()):
            p.unlink()
    except Exception:
        pass


def load_settings():
    try:
        return json.loads(config_path().read_text(encoding="utf-8"))
    except Exception:
        return {"city": DEFAULT_CITY, "utc_offset": DEFAULT_UTC_OFFSET}


def save_settings(data):
    try:
        config_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "FerrariF1CyberWidget/1.0"})
    with urllib.request.urlopen(req, timeout=12) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_weather(city):
    q = urllib.parse.urlencode({"name": city, "count": 1, "language": "en", "format": "json"})
    geo = get_json("https://geocoding-api.open-meteo.com/v1/search?" + q)
    results = geo.get("results") or []
    if not results:
        raise ValueError("City not found. Try another city name.")
    place = results[0]
    params = {
        "latitude": place["latitude"],
        "longitude": place["longitude"],
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,is_day",
        "timezone": "auto"
    }
    weather = get_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params))
    current = weather.get("current", {})
    return {
        "city": place.get("name", city),
        "country": place.get("country", ""),
        "utc_offset": int(weather.get("utc_offset_seconds", DEFAULT_UTC_OFFSET)),
        "temp": round(current.get("temperature_2m", 0)),
        "feels": round(current.get("apparent_temperature", 0)),
        "humidity": current.get("relative_humidity_2m", "--"),
        "code": current.get("weather_code", 0),
        "is_day": current.get("is_day", 1)
    }


def weather_label(code):
    if code == 0: return "CLEAR SKY"
    if code in (1, 2): return "PARTLY CLOUDY"
    if code == 3: return "OVERCAST"
    if code in (45, 48): return "FOG"
    if code in (51, 53, 55, 56, 57): return "DRIZZLE"
    if code in (61, 63, 65, 66, 67, 80, 81, 82): return "RAIN"
    if code in (71, 73, 75, 77, 85, 86): return "SNOW"
    if code in (95, 96, 99): return "THUNDERSTORM"
    return "WEATHER UPDATE"


def weather_symbol(code, is_day=1):
    if code == 0: return "☀" if is_day else "☾"
    if code in (1, 2): return "◐"
    if code in (3, 45, 48): return "☁"
    if code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82): return "☂"
    if code in (71, 73, 75, 77, 85, 86): return "❄"
    if code in (95, 96, 99): return "ϟ"
    return "☁"


class FerrariWidget:
    def __init__(self):
        self.settings = load_settings()
        self.city = self.settings.get("city", DEFAULT_CITY)
        self.utc_offset = int(self.settings.get("utc_offset", DEFAULT_UTC_OFFSET))
        self.weather = None
        self.embedded = False
        self.hwnd = None

        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.overrideredirect(True)
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.geometry(self.initial_geometry())

        self.canvas = tk.Canvas(self.root, width=WIN_W, height=WIN_H, bg=BG,
                                highlightthickness=0, bd=0)
        self.canvas.pack()
        self.draw_static()
        self.bind_events()
        self.tick()
        self.refresh_weather()

        if DESKTOP_MODE:
            self.root.after(150, self.make_desktop_level)
            self.root.after(2000, self.keep_on_desktop)

    # ---------- 定位：精确嵌入壁纸分区框 ----------
    def target_position(self):
        sw = self.root.winfo_screenwidth()
        return sw - WIN_W - RIGHT_MARGIN, TOP_MARGIN

    def initial_geometry(self):
        x, y = self.target_position()
        return f"{WIN_W}x{WIN_H}+{x}+{y}"

    def apply_octagon_shape(self):
        """用窗口区域把窗口真正裁成八边形 —— 四个切角完全不存在，不会露黑块。"""
        if not self.hwnd:
            return
        k = CUT
        pts = [(0, k), (k, 0), (WIN_W - k, 0), (WIN_W, k),
               (WIN_W, WIN_H - k), (WIN_W - k, WIN_H), (k, WIN_H), (0, WIN_H - k)]
        arr = (wintypes.POINT * len(pts))(*[wintypes.POINT(x, y) for x, y in pts])
        hrgn = G32.CreatePolygonRgn(arr, len(pts), 2)  # 2 = WINDING
        if hrgn:
            U32.SetWindowRgn(self.hwnd, hrgn, True)

    def make_desktop_level(self):
        """桌面级窗口：无任务栏图标、不进 Alt+Tab、不遮挡应用（也不被最小化）。"""
        try:
            self.hwnd = U32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            ex = _get_long(self.hwnd, GWL_EXSTYLE)
            ex = (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
            _set_long(self.hwnd, GWL_EXSTYLE, ex)
            self.apply_octagon_shape()
            self.embedded = True
            self.canvas.itemconfigure(self.pin_id, state="hidden")
            self.canvas.itemconfigure(self.mode_text, state="normal")
            self.keep_floating()
        except Exception:
            self.embedded = False
            import traceback
            log_error("desktop-level failed: " + traceback.format_exc())

    def keep_floating(self):
        if not self.hwnd:
            return
        U32.SetWindowPos(self.hwnd, wintypes.HWND(HWND_NOTOPMOST), 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def keep_on_desktop(self):
        if DESKTOP_MODE:
            try:
                if not self.embedded:
                    self.make_desktop_level()
                elif self.hwnd and U32.IsIconic(self.hwnd):
                    U32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
            except Exception:
                pass
        self.root.after(2000, self.keep_on_desktop)

    # ---------- 界面 ----------
    def draw_static(self):
        c = self.canvas
        c.delete("all")
        W, H, k = WIN_W, WIN_H, CUT

        # 八边形面板（切角与壁纸框一致）
        c.create_polygon(0, k, k, 0, W - k, 0, W, k, W, H - k, W - k, H, k, H, 0, H - k,
                         fill=PANEL, outline=RED_DARK, width=1)

        # 边框高光：左上红条 + 右下红条（呼应壁纸框的装饰）
        c.create_line(1, k + 4, 1, 24, fill=RED, width=2)
        c.create_line(k + 2, 1, 58, 1, fill=RED, width=2)
        c.create_line(W - 1, H - k - 6, W - 1, H - 26, fill=RED, width=2)
        c.create_line(W - k - 2, H - 1, W - 60, H - 1, fill=RED, width=2)
        # 右上/左下 细斜切角装饰
        c.create_line(W - k + 1, 1, W - 1, k + 1, fill=RED, width=1)
        c.create_line(1, H - k - 1, k + 1, H - 1, fill=RED_DARK, width=1)

        # 细分割线
        for yy in (40, 124):
            c.create_line(k + 2, yy, W - k - 2, yy, fill="#2a1a20", width=1)

        # 标题区
        c.create_text(16, 15, anchor="w", text="/// SYSTEM INFO",
                      fill=WHITE, font=("Segoe UI", 7, "bold"))
        self.f1_text = c.create_text(W - 30, 15, anchor="e", text="F1 // 16",
                      fill=RED, font=("Segoe UI", 6, "bold"))
        c.create_text(16, 30, anchor="w", text="CLOCK • WEATHER",
                      fill=MUTED, font=("Segoe UI", 6, "bold"))
        self.mode_text = c.create_text(W - 16, 30, anchor="e", text="DESKTOP•LINK",
                      fill="#5d4a52", font=("Segoe UI", 5, "bold"), state="hidden")

        # 时钟
        self.clock_text = c.create_text(12, 86, anchor="w", text="00:00",
                      fill=WHITE, font=("Segoe UI", 29, "bold"))
        self.seconds_text = c.create_text(W - 14, 72, anchor="e", text="00",
                      fill=CYAN, font=("Segoe UI", 10, "bold"))
        self.date_text = c.create_text(16, 112, anchor="w", text="2026 / 11 / 15  SUN.",
                      fill="#c9c2c7", font=("Segoe UI", 7))

        # 天气
        c.create_text(16, 138, anchor="w", text="LIVE WEATHER",
                      fill=MUTED, font=("Segoe UI", 6, "bold"))
        self.weather_icon = c.create_text(34, 172, anchor="center", text="☁",
                      fill=WHITE, font=("Segoe UI Symbol", 21))
        self.temp_text = c.create_text(62, 168, anchor="w", text="--°C",
                      fill=WHITE, font=("Segoe UI", 19, "bold"))
        self.condition_text = c.create_text(63, 188, anchor="w", text="FETCHING",
                      fill=RED, font=("Segoe UI", 6, "bold"))
        self.location_text = c.create_text(16, 207, anchor="w", text=self.city.upper(),
                      fill="#e2dce0", font=("Segoe UI", 7, "bold"))
        self.details_text = c.create_text(W - 16, 207, anchor="e", text="FEELS --°  HUM --%",
                      fill=MUTED, font=("Segoe UI", 5))
        self.status_text = c.create_text(16, 226, anchor="w", text="● LINK: INITIALIZING",
                      fill=CYAN, font=("Segoe UI", 5, "bold"))

        # 关闭按钮
        self.close_id = c.create_text(W - 16, 6, anchor="ne", text="×",
                      fill="#ff5362", font=("Segoe UI", 10, "bold"), tags="close")
        self.pin_id = c.create_text(W - 32, 6, anchor="ne", text="◆",
                      fill=CYAN, font=("Segoe UI", 7), tags="pin", state="hidden")

    def bind_events(self):
        c = self.canvas
        # 注意：不再绑定拖拽，位置固定
        c.tag_bind("close", "<Button-1>", lambda e: self.close())
        c.bind("<Double-Button-1>", lambda e: self.change_city())
        c.bind("<Button-3>", lambda e: self.change_city())
        self.root.bind("<Escape>", lambda e: self.close())

    def change_city(self):
        city = simpledialog.askstring(APP_NAME, "Enter city for weather:",
                                      initialvalue=self.city, parent=self.root)
        if city and city.strip():
            self.city = city.strip()
            self.settings["city"] = self.city
            self.canvas.itemconfigure(self.location_text, text=self.city.upper())
            save_settings(self.settings)
            self.refresh_weather()

    def tick(self):
        now = datetime.now(timezone.utc) + timedelta(seconds=self.utc_offset)
        self.canvas.itemconfigure(self.clock_text, text=now.strftime("%H:%M"))
        self.canvas.itemconfigure(self.seconds_text, text=":" + now.strftime("%S"))
        self.canvas.itemconfigure(self.date_text, text=now.strftime("%Y / %m / %d   %a").upper())
        self.root.after(500, self.tick)

    def refresh_weather(self):
        self.canvas.itemconfigure(self.status_text, text="● LINK: REQUESTING DATA", fill=CYAN)

        def worker():
            try:
                data = fetch_weather(self.city)
                self.root.after(0, lambda d=data: self.show_weather(d))
            except Exception as exc:
                msg = str(exc)
                self.root.after(0, lambda m=msg: self.weather_error(m))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(15 * 60 * 1000, self.refresh_weather)

    def show_weather(self, data):
        self.weather = data
        self.utc_offset = int(data.get("utc_offset", self.utc_offset))
        self.settings["utc_offset"] = self.utc_offset
        save_settings(self.settings)
        self.canvas.itemconfigure(self.location_text, text=data["city"].upper())
        self.canvas.itemconfigure(self.temp_text, text=f'{data["temp"]}°C')
        self.canvas.itemconfigure(self.weather_icon, text=weather_symbol(data["code"], data["is_day"]))
        self.canvas.itemconfigure(self.condition_text, text=weather_label(data["code"]))
        self.canvas.itemconfigure(self.details_text,
            text=f'FEELS {data["feels"]}° / HUM {data["humidity"]}%')
        self.canvas.itemconfigure(self.status_text, text="● LINK: ONLINE // 15M", fill=CYAN)

    def weather_error(self, message):
        self.canvas.itemconfigure(self.status_text, text="● LINK: OFFLINE", fill=RED)
        self.canvas.itemconfigure(self.condition_text, text="WEATHER UNAVAILABLE")

    def close(self):
        clear_pid()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if not single_instance():
        sys.exit(0)
    write_pid()
    try:
        app = FerrariWidget()
        app.root.report_callback_exception = lambda *a: log_error(
            "callback: " + "".join(__import__("traceback").format_exception(*a)))
        app.run()
    except Exception:
        import traceback
        log_error("fatal:\n" + traceback.format_exc())
        raise
