# -*- coding: utf-8 -*-
"""
Ferrari F1 Cyber Widget - RACE TELEMETRY panel
------------------------------------------------
左下角赛道遥测面板：CPU / GPU / MEMORY / NETWORK 实时数据 + CPU·GPU 动态曲线。

设计尺寸严格对齐壁纸左下角 MEDIA PLAYER 分区框
（实测物理 622x110 @125% 缩放 => 逻辑 498x88）。

全部指标用 Windows 原生 API 读取，不依赖任何第三方库：
  CPU   GetSystemTimes
  RAM   GlobalMemoryStatusEx
  NET   GetIfTable
  GPU   nvidia-smi（无 NVIDIA 显卡时显示 N/A，绝不伪造数据）
"""

import ctypes
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from ctypes import wintypes
from pathlib import Path

import tkinter as tk

# ----------------------------------------------------------------------------
# 基本参数
# ----------------------------------------------------------------------------
APP_NAME = "Ferrari F1 Race Telemetry"
CONFIG_FOLDER = "FerrariF1Telemetry"      # 独立配置目录，不影响另外两个组件

WIN_W, WIN_H = 498, 88                    # 逻辑像素，与壁纸框 1:1 对齐
CUT = 12                                  # 八边形切角
DEFAULT_LEFT, DEFAULT_BOTTOM_GAP = 22, 143  # 默认位置：距左 22，距屏底 143

BG = "#07070a"
RED = "#e01b2c"
RED_SOFT = "#ff3b4a"
RED_DARK = "#5c0f18"
WHITE = "#f4f0f2"
MUTED = "#8b7f86"
FAINT = "#2c2024"
CYAN = "#2ee6d6"
GPU_LINE = "#ff7a5c"

# ----------------------------------------------------------------------------
# Win32
# ----------------------------------------------------------------------------
U32 = ctypes.WinDLL("user32", use_last_error=True)
K32 = ctypes.WinDLL("kernel32", use_last_error=True)
G32 = ctypes.WinDLL("gdi32", use_last_error=True)
IPH = ctypes.WinDLL("iphlpapi", use_last_error=True)

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


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD),
                ("dwHighDateTime", wintypes.DWORD)]


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


MAX_INTERFACE_NAME_LEN = 256
MAXLEN_PHYSADDR = 8
MAXLEN_IFDESCR = 256


class MIB_IFROW(ctypes.Structure):
    _fields_ = [("wszName", wintypes.WCHAR * MAX_INTERFACE_NAME_LEN),
                ("dwIndex", wintypes.DWORD),
                ("dwType", wintypes.DWORD),
                ("dwMtu", wintypes.DWORD),
                ("dwSpeed", wintypes.DWORD),
                ("dwPhysAddrLen", wintypes.DWORD),
                ("bPhysAddr", ctypes.c_ubyte * MAXLEN_PHYSADDR),
                ("dwAdminStatus", wintypes.DWORD),
                ("dwOperStatus", wintypes.DWORD),
                ("dwLastChange", wintypes.DWORD),
                ("dwInOctets", wintypes.DWORD),
                ("dwInUcastPkts", wintypes.DWORD),
                ("dwInNUcastPkts", wintypes.DWORD),
                ("dwInDiscards", wintypes.DWORD),
                ("dwInErrors", wintypes.DWORD),
                ("dwInUnknownProtos", wintypes.DWORD),
                ("dwOutOctets", wintypes.DWORD),
                ("dwOutUcastPkts", wintypes.DWORD),
                ("dwOutNUcastPkts", wintypes.DWORD),
                ("dwOutDiscards", wintypes.DWORD),
                ("dwOutErrors", wintypes.DWORD),
                ("dwOutQLen", wintypes.DWORD),
                ("dwDescrLen", wintypes.DWORD),
                ("bDescr", ctypes.c_ubyte * MAXLEN_IFDESCR)]


class MIB_IFTABLE(ctypes.Structure):
    _fields_ = [("dwNumEntries", wintypes.DWORD),
                ("table", MIB_IFROW * 1)]


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


def config_path():
    return config_dir() / "settings.json"


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


DEFAULT_SETTINGS = {
    "x": None, "y": None, "topmost": False,
    # 分指标阈值：Windows 会把空闲内存拿去做缓存，内存 60~75% 属于常态，
    # 所以内存的门槛必须比 CPU/GPU 高一档，否则旗语永远是黄的。
    "cpu_yellow": 70, "cpu_red": 90,
    "gpu_yellow": 75, "gpu_red": 92,
    "ram_yellow": 85, "ram_red": 93,
}

# F1 旗语（按 CPU/GPU/内存 的峰值负载判定，阈值可在 settings.json 里改）
FLAG_GREEN = "#39d353"
FLAG_YELLOW = "#f5c518"
FLAG_RED = "#ff2d3f"


def load_settings():
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
        # 老版本单一阈值自动迁移（不影响新配置）
        if "yellow_at" in data and "cpu_yellow" not in data:
            y = int(data.get("yellow_at", 60))
            r = int(data.get("red_at", 85))
            data.setdefault("cpu_yellow", y)
            data.setdefault("cpu_red", r)
            data.setdefault("gpu_yellow", min(y + 5, 95))
            data.setdefault("gpu_red", min(r + 2, 97))
            data.setdefault("ram_yellow", min(y + 25, 95))
            data.setdefault("ram_red", min(r + 8, 97))
        merged = dict(DEFAULT_SETTINGS)
        merged.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
        return merged
    except Exception:
        return dict(DEFAULT_SETTINGS)


def save_settings(s):
    try:
        config_path().write_text(json.dumps(s, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    except Exception:
        log_error("save settings failed: " + traceback.format_exc())


# ----------------------------------------------------------------------------
# 指标采集
# ----------------------------------------------------------------------------
class Metrics:
    """纯 Win32 指标采集器（无需 psutil）。"""

    def __init__(self):
        self._prev_idle = None
        self._prev_kern = None
        self._prev_user = None
        self._prev_rx = None
        self._prev_tx = None
        self._prev_ts = None
        self.cpu = 0.0
        self.gpu = None          # None = 不可用
        self.gpu_temp = None
        self.ram_pct = 0.0
        self.ram_used_gb = 0.0
        self.ram_total_gb = 0.0
        self.rx_bps = 0.0
        self.tx_bps = 0.0
        self._gpu_ready = False

        self._smi = shutil.which("nvidia-smi")
        if not self._smi:
            for cand in (r"C:\Windows\System32\nvidia-smi.exe",
                         r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"):
                if os.path.exists(cand):
                    self._smi = cand
                    break
        self._gpu_ready = bool(self._smi)
        if self._gpu_ready:
            t = threading.Thread(target=self._gpu_loop, daemon=True)
            t.start()

    # ---- GPU（后台线程，避免阻塞界面）----
    def _gpu_loop(self):
        flags = 0x08000000 if os.name == "nt" else 0     # CREATE_NO_WINDOW
        while True:
            try:
                out = subprocess.run(
                    [self._smi, "--query-gpu=utilization.gpu,temperature.gpu",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5,
                    creationflags=flags)
                line = (out.stdout or "").strip().splitlines()
                if line:
                    parts = [p.strip() for p in line[0].split(",")]
                    util = float(parts[0]) if parts[0] not in ("", "[N/A]") else None
                    temp = float(parts[1]) if len(parts) > 1 and parts[1] not in ("", "[N/A]") else None
                    self.gpu = util
                    self.gpu_temp = temp
                else:
                    self.gpu = None
            except Exception:
                self.gpu = None
            time.sleep(2.0)

    # ---- CPU ----
    def _read_cpu(self):
        idle, kern, user = FILETIME(), FILETIME(), FILETIME()
        if not K32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern),
                                  ctypes.byref(user)):
            return None
        conv = lambda ft: (ft.dwHighDateTime << 32) | ft.dwLowDateTime
        return conv(idle), conv(kern), conv(user)

    def _sample_cpu(self):
        cur = self._read_cpu()
        if cur is None:
            return
        idle, kern, user = cur
        if self._prev_idle is not None:
            d_idle = idle - self._prev_idle
            d_total = (kern - self._prev_kern) + (user - self._prev_user)
            if d_total > 0:
                self.cpu = max(0.0, min(100.0, 100.0 * (1.0 - d_idle / d_total)))
        self._prev_idle, self._prev_kern, self._prev_user = idle, kern, user

    # ---- RAM ----
    def _sample_ram(self):
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if K32.GlobalMemoryStatusEx(ctypes.byref(st)):
            self.ram_pct = float(st.dwMemoryLoad)
            total = st.ullTotalPhys / (1024 ** 3)
            used = (st.ullTotalPhys - st.ullAvailPhys) / (1024 ** 3)
            self.ram_total_gb = total
            self.ram_used_gb = used

    # ---- NET ----
    def _read_net(self):
        """返回 {接口索引: (累计接收字节, 累计发送字节)}。

        注意：GetIfTable 会把同一块网卡的 WFP / QoS / 虚拟过滤层各列一行，
        且这些行的计数完全相同。所以这里不做求和（会翻好几倍），
        而是逐接口比较增量、取流量最大的那块网卡。
        """
        size = wintypes.DWORD(0)
        IPH.GetIfTable(None, ctypes.byref(size), False)
        if size.value == 0:
            return None
        buf = ctypes.create_string_buffer(size.value)
        if IPH.GetIfTable(ctypes.byref(buf), ctypes.byref(size), False) != 0:
            return None
        table = ctypes.cast(buf, ctypes.POINTER(MIB_IFTABLE)).contents
        n = table.dwNumEntries
        if n <= 0:
            return None
        rows = ctypes.cast(ctypes.byref(table.table),
                           ctypes.POINTER(MIB_IFROW * n)).contents
        out = {}
        for row in rows:
            if row.dwType == 24:              # loopback
                continue
            if row.dwSpeed == 0:              # 未启用
                continue
            out[row.dwIndex] = (row.dwInOctets & 0xFFFFFFFF,
                                row.dwOutOctets & 0xFFFFFFFF)
        return out or None

    def _sample_net(self):
        cur = self._read_net()
        now = time.time()
        if not cur:
            return
        if self._prev_rx and self._prev_ts:
            dt = max(0.2, now - self._prev_ts)
            best = None
            for idx, (rx, tx) in cur.items():
                if idx not in self._prev_rx:
                    continue
                prx, ptx = self._prev_rx[idx]
                drx = ((rx - prx) & 0xFFFFFFFF) / dt
                dtx = ((tx - ptx) & 0xFFFFFFFF) / dt
                score = drx + dtx
                if best is None or score > best[0]:
                    best = (score, drx, dtx)
            if best:
                self.rx_bps, self.tx_bps = best[1], best[2]
        self._prev_rx = cur
        self._prev_ts = now

    def sample(self):
        self._sample_cpu()
        self._sample_ram()
        self._sample_net()
        return self


def fmt_rate(bps):
    """字节/秒 -> 人类可读 (MB/s 或 KB/s)"""
    if bps >= 1024 * 1024:
        return f"{bps / (1024 * 1024):.1f}", "MB/s"
    if bps >= 1024:
        return f"{bps / 1024:.0f}", "KB/s"
    return f"{bps:.0f}", "B/s"


def fmt_pair(down_bps, up_bps):
    """上下行用同一个单位显示，返回 (文本, 单位)。"""
    peak = max(down_bps, up_bps)
    if peak >= 1024 * 1024:
        div, unit, dec = 1024 ** 2, "MB/s", 1
    elif peak >= 1024:
        div, unit, dec = 1024, "KB/s", 0
    else:
        div, unit, dec = 1, "B/s", 0
    return f"↓{down_bps / div:.{dec}f} ↑{up_bps / div:.{dec}f}", unit


# ----------------------------------------------------------------------------
# 界面
# ----------------------------------------------------------------------------
class TelemetryPanel:
    def __init__(self):
        self.settings = load_settings()
        self.metrics = Metrics()
        self.cpu_hist = deque([0.0] * 62, maxlen=62)
        self.gpu_hist = deque([0.0] * 62, maxlen=62)
        self.hwnd = None

        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.overrideredirect(True)
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", bool(self.settings.get("topmost", False)))
        self.root.geometry(self.initial_geometry())

        self.canvas = tk.Canvas(self.root, width=WIN_W, height=WIN_H, bg=BG,
                                highlightthickness=0, bd=0)
        self.canvas.pack()
        self.build()
        self.bind_events()
        self.root.after(90, self.apply_window_shape)
        self.root.after(200, self.tick)

    # ---------- 几何 ----------
    def initial_geometry(self):
        x, y = self.settings.get("x"), self.settings.get("y")
        if x is None or y is None:
            sw = self.root.winfo_screenwidth() if hasattr(self, "root") else 1920
            sh = self.root.winfo_screenheight() if hasattr(self, "root") else 1080
            x, y = DEFAULT_LEFT, sh - DEFAULT_BOTTOM_GAP
        return f"{WIN_W}x{WIN_H}+{int(x)}+{int(y)}"

    def position(self):
        return self.root.winfo_x(), self.root.winfo_y()

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

    # ---------- 窗口形状（八边形真透明）----------
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
        W, H = WIN_W, WIN_H

        # 外框（八边形）
        pts, _ = self.octagon(1.0)
        c.create_polygon(*[v for p in pts for v in p], fill=BG, outline=RED_DARK,
                         width=1)
        _, flat = self.octagon(3.0)
        c.create_polygon(flat, fill="", outline=RED, width=1)

        # 左上 / 右下红色装饰条
        c.create_line(22, 2, 96, 2, fill=RED_SOFT, width=2)

        # ---- 曲线区（先画，保证文字在上层）----
        self.spark_top, self.spark_bot = 62, 84
        for gx in range(14, W - 14, 39):
            c.create_line(gx, self.spark_top, gx, self.spark_bot, fill="#150d10",
                          width=1)
        c.create_line(14, self.spark_bot, W - 14, self.spark_bot, fill="#2a161b",
                      width=1)
        self.cpu_fill = c.create_polygon(0, 0, 0, 0, fill="#4a1019", outline="")
        self.gpu_line = c.create_line(0, 0, 1, 1, fill=GPU_LINE, width=1)
        self.cpu_line = c.create_line(0, 0, 1, 1, fill=RED_SOFT, width=2)
        self.cpu_head = c.create_oval(-4, -4, -4, -4, fill=RED_SOFT, outline="")

        # ---- 头部 ----
        c.create_text(14, 13, anchor="w", text="///", fill=RED,
                      font=("Segoe UI", 8, "bold"))
        c.create_text(37, 13, anchor="w", text="RACE TELEMETRY", fill=WHITE,
                      font=("Segoe UI", 9, "bold"))
        c.create_text(172, 14, anchor="w", text="· SCUDERIA FERRARI ·", fill="#6a4249",
                      font=("Segoe UI", 5))
        self.live_text = c.create_text(W - 34, 13, anchor="e", text="● LINK",
                                       fill=CYAN, font=("Segoe UI", 6, "bold"))
        self.close_btn = c.create_text(W - 14, 13, anchor="e", text="✕", fill=MUTED,
                                       font=("Segoe UI", 8, "bold"))
        c.create_line(14, 22, W - 14, 22, fill=FAINT, width=1)

        # ---- 四列指标 ----
        col_w = (W - 28) / 4.0
        labels = ("CPU LOAD", "GPU LOAD", "MEMORY", "NETWORK")
        big_font = ("Segoe UI", 16, "bold")
        net_font = ("Segoe UI", 12, "bold")
        self.value_items, self.aux_items = [], []
        for i, lab in enumerate(labels):
            x = 14 + i * col_w
            if i:
                c.create_line(x - 8, 27, x - 8, 57, fill=FAINT, width=1)
            c.create_text(x, 31, anchor="w", text=lab, fill=MUTED,
                          font=("Segoe UI", 6, "bold"))
            a = c.create_text(x + col_w - 18, 31, anchor="e", text="", fill=MUTED,
                              font=("Segoe UI", 6))
            v = c.create_text(x, 50, anchor="w", text="--", fill=WHITE,
                              font=(net_font if i == 3 else big_font))
            self.value_items.append(v)
            self.aux_items.append(a)

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
        self.menu.add_command(label="关闭面板", command=self.close)

    def on_close_click(self, e):
        self.close()
        return "break"

    def on_menu(self, e):
        self.menu.tk_popup(e.x_root, e.y_root)

    # ---------- 数据刷新 ----------
    def tick(self):
        try:
            m = self.metrics.sample()

            self.cpu_hist.append(m.cpu)
            self.gpu_hist.append(m.gpu if m.gpu is not None else 0.0)

            self.canvas.itemconfigure(self.value_items[0], text=f"{m.cpu:.0f}%")
            self.canvas.itemconfigure(self.aux_items[0],
                                      text=f"{os.cpu_count() or 0}C")

            if m.gpu is None:
                self.canvas.itemconfigure(self.value_items[1], text="N/A",
                                          fill=MUTED)
                self.canvas.itemconfigure(self.aux_items[1], text="")
            else:
                self.canvas.itemconfigure(self.value_items[1], text=f"{m.gpu:.0f}%",
                                          fill=WHITE)
                self.canvas.itemconfigure(
                    self.aux_items[1],
                    text=(f"{m.gpu_temp:.0f}°C" if m.gpu_temp is not None else ""))

            self.canvas.itemconfigure(self.value_items[2], text=f"{m.ram_pct:.0f}%")
            self.canvas.itemconfigure(
                self.aux_items[2], text=f"{m.ram_used_gb:.1f}/{m.ram_total_gb:.0f}G")

            net_text, unit = fmt_pair(m.rx_bps, m.tx_bps)
            self.canvas.itemconfigure(self.value_items[3], text=net_text)
            self.canvas.itemconfigure(self.aux_items[3], text=unit)

            self.draw_spark()
            self.update_flag(m)
        except Exception:
            log_error("tick failed: " + traceback.format_exc())

        self.root.after(1000, self.tick)

    def update_flag(self, m):
        """F1 旗语：按 CPU / GPU / 内存 各自的门槛分别判定，取最严重的一个。

        内存门槛故意更高（默认 85/93）：Windows 会用空闲内存做缓存，
        16G 机器日常 60~75% 属于正常水位，不该报警。
        """
        s = self.settings

        def level(value, y_key, r_key):
            if value is None:
                return 0
            if value >= s.get(r_key, 90):
                return 2
            if value >= s.get(y_key, 70):
                return 1
            return 0

        checks = [
            ("CPU", m.cpu, level(m.cpu, "cpu_yellow", "cpu_red")),
            ("GPU", m.gpu, level(m.gpu, "gpu_yellow", "gpu_red")),
            ("RAM", m.ram_pct, level(m.ram_pct, "ram_yellow", "ram_red")),
        ]
        worst = max(checks, key=lambda c: c[2])

        if worst[2] >= 2:
            text, color = "▲ RED FLAG", FLAG_RED
        elif worst[2] == 1:
            text, color = "▲ YELLOW FLAG", FLAG_YELLOW
        else:
            text, color = "▲ GREEN FLAG", FLAG_GREEN

        if worst[2] > 0:
            text += f" · {worst[0]} {worst[1]:.0f}%"

        self.canvas.itemconfigure(self.live_text, text=text, fill=color)

    def draw_spark(self):
        x0, x1 = 14, WIN_W - 14
        top, bot = self.spark_top, self.spark_bot
        span = max(1, x1 - x0)

        # 自适应量程：按最近 62 秒的峰值向上取整到 25/50/75/100，
        # 低负载时曲线也不会贴着底边看不见（25% 下限避免放大噪声）
        peak = max(max(self.cpu_hist), max(self.gpu_hist), 1.0)
        ceiling = 100.0
        for step in (25.0, 50.0, 75.0, 100.0):
            if peak <= step:
                ceiling = step
                break

        def y_of(v):
            v = max(0.0, min(100.0, v))
            return bot - (v / ceiling) * (bot - top)

        def pts(hist):
            n = len(hist)
            step = span / max(1, n - 1)
            out = []
            for i, v in enumerate(hist):
                out.extend([x0 + i * step, y_of(v)])
            return out

        self.canvas.coords(self.gpu_line, *pts(self.gpu_hist))
        cpu_pts = pts(self.cpu_hist)
        self.canvas.coords(self.cpu_line, *cpu_pts)
        self.canvas.coords(self.cpu_fill, *([x0, bot] + cpu_pts + [x1, bot]))

        head_y = y_of(self.cpu_hist[-1])
        self.canvas.coords(self.cpu_head, x1 - 2, head_y - 2, x1 + 2, head_y + 2)

    # ---------- 生命周期 ----------
    def close(self):
        try:
            self.settings["x"], self.settings["y"] = self.position()
            save_settings(self.settings)
        except Exception:
            pass
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
    """全局命名互斥体，防止重复启动出现两个面板。"""
    K32.CreateMutexW.restype = wintypes.HANDLE
    K32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    handle = K32.CreateMutexW(None, False, "Global\\FerrariF1RaceTelemetry")
    if not handle:
        return True
    globals()["_mutex_handle"] = handle          # 保活，防止 GC 关闭句柄
    return ctypes.get_last_error() != 183        # 183 = ERROR_ALREADY_EXISTS


def main():
    if not single_instance():
        sys.exit(0)
    try:
        app = TelemetryPanel()
        app.run()
    except Exception:
        log_error("fatal: " + traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
