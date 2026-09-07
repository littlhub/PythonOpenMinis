# -*- coding: utf-8 -*-
"""showtagger.py —— LLM 计费监控（Tkinter）

两种视图，顶部按钮切换：
  · [按调用]  折线图：x=第N次调用(时间刻度 HH:MM:SS)，y=tokens，按模型分色
  · [每日/周/月]  热力图（GitHub 风格）：7×N 方格网，每天一格，颜色按用量等级分档
    - 每日：行=周一~周日，列=周，x 轴下方标月份
    - 每周：行=月内周次，列=月，x 轴下方标年份
    - 每月：行=1~12 月，列=年，x 轴下方标年份

深色主题：背景 #0d1117，5 档蓝紫色阶（无/低/中/高/极高）。
实时读取 llm计费.json，每 1 秒轮询，文件原子写后立即重载。

用法：
    python  showtagger.py            # 自动探测工作区
    python  showtagger.py <工作区目录>
    pythonw showtagger.py            # Windows 无控制台
"""

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

# ---------- 数据层（纯函数） ----------

def resolve_workspace(arg: str = None) -> str:
    if arg:
        return arg
    env = os.environ.get("SKILLMCP_WORKSPACE")
    if env and os.path.isdir(env):
        return env
    # 本文件位于 <项目根>/mcp/tools/，向上三级定位项目根（独立运行也能找到工作区）
    proj = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    for cand in (os.path.join(proj, "workspase"),
                 os.path.join(os.getcwd(), "workspase")):
        if os.path.isdir(cand):
            return cand
    return os.path.join(os.getcwd(), "workspase")


def load_bill(path: str):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        records = data.get("records")
        if not isinstance(records, list) or not records:
            return None
        return data
    except Exception:
        return None


def _parse_time(t: str):
    try:
        return datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def prepare_series(records: list):
    """按调用折线图：[{model, color_idx, points:[(idx, total, time_str)]}] 与 y_max。"""
    order, groups = [], {}
    y_max = 1
    for i, r in enumerate(records, 1):
        model = str(r.get("model") or "unknown")
        total = int(r.get("prompt_tokens", 0) or 0) + int(r.get("completion_tokens", 0) or 0)
        if model not in groups:
            groups[model] = {}
            order.append(model)
        groups[model][i] = (total, str(r.get("time", "")))
        if total > y_max:
            y_max = total
    series = []
    for color_idx, model in enumerate(order):
        g = groups[model]
        points = [(i, g[i][0], g[i][1]) for i in sorted(g)]
        series.append({"model": model, "color_idx": color_idx, "points": points})
    return series, y_max


# ---------- 热力图数据层 ----------

WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
MONTH_LABELS = [f"{i}月" for i in range(1, 13)]


def _monday(d: datetime) -> datetime:
    """返回 d 所在周的周一（00:00:00）。"""
    return d - timedelta(days=d.weekday())


def aggregate_grid(records: list, grain: str):
    r"""把 records 按粒度铺成二维网格。

    grain ∈ {"day", "week", "month"}：
      · day  —— 行 = 周一~周日(7)，列 = 整年跨度中的周序号
      · week —— 行 = 月内第 1~6 周(6)，列 = 月序号
      · month —— 行 = 1~12 月(12)，列 = 年序号

    返回 (grid, n_rows, n_cols, x_axis, y_labels, x_axis_title, max_total, total_in, total_out)
    - grid[(row, col)] = {total, n, in, out, sample, label}
    - x_axis = [(col_idx, label_text, align)] —— 月份标签（对齐到该 col 下方）
    - y_labels = [row_idx → label_text]
    - x_axis_title 提示 "按月" / "按年" / "按周" 之类
    """
    # 过滤能解析的记录
    parsed = []
    for r in records:
        dt = _parse_time(str(r.get("time", "")))
        if dt is None:
            continue
        parsed.append((dt, r))
    if not parsed:
        return {}, (7 if grain == "day" else (6 if grain == "week" else 12)), 0, [], [], "", 1, 0, 0

    total_in = sum(int(r.get("prompt_tokens", 0) or 0) for _, r in parsed)
    total_out = sum(int(r.get("completion_tokens", 0) or 0) for _, r in parsed)

    if grain == "day":
        # 按 ISO 周：col = (本周一 - 最早周一).days // 7
        first_monday = _monday(min(dt for dt, _ in parsed))
        buckets = defaultdict(lambda: {"in": 0, "out": 0, "n": 0, "sample": ""})
        col_labels = {}        # col_idx -> (label_text, 该 col 中心对应日期)
        col_first_dt = {}      # col_idx -> 该 col 周一日期
        for dt, r in parsed:
            monday = _monday(dt)
            col = (monday - first_monday).days // 7
            row = dt.weekday()       # 0=周一..6=周日
            b = buckets[(row, col)]
            b["in"] += int(r.get("prompt_tokens", 0) or 0)
            b["out"] += int(r.get("completion_tokens", 0) or 0)
            b["n"] += 1
            if not b["sample"]:
                b["sample"] = dt.strftime("%Y-%m-%d %H:%M:%S")
                b["dt"] = dt
            if col not in col_first_dt or monday < col_first_dt[col]:
                col_first_dt[col] = monday
        # 每行格子：grid[(row, col)]，补上 label（日期字符串）
        grid = {}
        max_total = 1
        for (row, col), b in buckets.items():
            total = b["in"] + b["out"]
            if total > max_total:
                max_total = total
            grid[(row, col)] = {
                "total": total, "n": b["n"],
                "in": b["in"], "out": b["out"],
                "sample": b["sample"],
                "label": b["dt"].strftime("%Y-%m-%d %a"),
            }
        n_cols = max(col for _, col in buckets) + 1 if buckets else 0
        # x 轴月份标签：每 col 下方是该 col 周一所在月份
        # 简化：每月只在该月第一个 col 下方显示月份
        x_axis = []
        prev_month = None
        for c in range(n_cols):
            monday = col_first_dt.get(c)
            if monday is None:
                continue
            mk = (monday.year, monday.month)
            if mk != prev_month:
                x_axis.append((c, f"{monday.month}月"))
                prev_month = mk
        y_labels = WEEKDAY_LABELS
        return grid, 7, n_cols, x_axis, y_labels, "时间", max_total, total_in, total_out

    if grain == "week":
        # 行：月内第 1~6 周（按 1~31 日的 //7 决定）
        # col：月序号（相对最早月）
        first_month = min(dt for dt, _ in parsed).replace(day=1)
        buckets = defaultdict(lambda: {"in": 0, "out": 0, "n": 0, "sample": ""})
        col_first_dt = {}
        for dt, r in parsed:
            col = (dt.year - first_month.year) * 12 + (dt.month - first_month.month)
            row = (dt.day - 1) // 7  # 0..4
            b = buckets[(row, col)]
            b["in"] += int(r.get("prompt_tokens", 0) or 0)
            b["out"] += int(r.get("completion_tokens", 0) or 0)
            b["n"] += 1
            if not b["sample"]:
                b["sample"] = dt.strftime("%Y-%m-%d %H:%M:%S")
                b["dt"] = dt
            if col not in col_first_dt or dt < col_first_dt[col]:
                col_first_dt[col] = dt
        grid = {}
        max_total = 1
        for (row, col), b in buckets.items():
            total = b["in"] + b["out"]
            if total > max_total:
                max_total = total
            week_start_day = row * 7 + 1
            week_end_day = min(week_start_day + 6, 28)
            grid[(row, col)] = {
                "total": total, "n": b["n"],
                "in": b["in"], "out": b["out"],
                "sample": b["sample"],
                "label": f"{b['dt'].year}-{b['dt'].month:02d} 第{row+1}周 ({week_start_day}~{week_end_day}日)",
            }
        n_cols = max(col for _, col in buckets) + 1 if buckets else 0
        x_axis = []
        for c in range(n_cols):
            dt = col_first_dt.get(c)
            if dt:
                x_axis.append((c, f"{dt.year}-{dt.month:02d}"))
        y_labels = [f"第{r+1}周" for r in range(6)]
        return grid, 6, n_cols, x_axis, y_labels, "月", max_total, total_in, total_out

    # grain == "month"
    first_year = min(dt.year for dt, _ in parsed)
    buckets = defaultdict(lambda: {"in": 0, "out": 0, "n": 0, "sample": ""})
    col_first_dt = {}
    for dt, r in parsed:
        col = dt.year - first_year
        row = dt.month - 1
        b = buckets[(row, col)]
        b["in"] += int(r.get("prompt_tokens", 0) or 0)
        b["out"] += int(r.get("completion_tokens", 0) or 0)
        b["n"] += 1
        if not b["sample"]:
            b["sample"] = dt.strftime("%Y-%m-%d %H:%M:%S")
            b["dt"] = dt
        if col not in col_first_dt or dt < col_first_dt[col]:
            col_first_dt[col] = dt
    grid = {}
    max_total = 1
    for (row, col), b in buckets.items():
        total = b["in"] + b["out"]
        if total > max_total:
            max_total = total
        grid[(row, col)] = {
            "total": total, "n": b["n"],
            "in": b["in"], "out": b["out"],
            "sample": b["sample"],
            "label": f"{b['dt'].year}年{b['dt'].month}月",
        }
    n_cols = max(col for _, col in buckets) + 1 if buckets else 0
    x_axis = [(c, str(first_year + c)) for c in range(n_cols)]
    y_labels = MONTH_LABELS
    return grid, 12, n_cols, x_axis, y_labels, "年", max_total, total_in, total_out


# ---------- 用量等级配色（深色主题，GitHub 风格蓝紫） ----------

# 5 档：低(深蓝) → 中(蓝) → 高(紫) → 极高(亮紫)
HEATMAP_COLORS = ["#1e3a5f", "#3b6ea5", "#5b8def", "#9b59ff", "#c084fc"]
LEVEL_LABELS = ["低", "中", "偏高", "高", "极高"]
NONE_COLOR = "#161b22"   # 无数据 / 当月无记录的格子
BG_COLOR = "#0d1117"     # 整体深色背景
GRID_LINE = "#21262d"
TEXT_COLOR = "#c9d1d9"
TEXT_DIM = "#8b949e"
LEVEL_COLORS_LIGHT = ["#2c5282", "#4a7fc1", "#7b9ef0", "#a78bfa", "#d8b4fe"]  # 图例浅色


def level_index(value: int, vmax: int) -> int:
    if vmax <= 0:
        return 0
    ratio = value / vmax
    if ratio <= 0:
        return -1   # 无数据
    if ratio < 0.2:
        return 0
    if ratio < 0.4:
        return 1
    if ratio < 0.6:
        return 2
    if ratio < 0.8:
        return 3
    return 4


def level_color(value: int, vmax: int) -> str:
    idx = level_index(value, vmax)
    if idx < 0:
        return NONE_COLOR
    return HEATMAP_COLORS[idx]


# ---------- Tk 界面 ----------

PALETTE = [
    "#e74c3c", "#3498db", "#2ecc71", "#f39c12", "#9b59b6",
    "#1abc9c", "#e67e22", "#34495e", "#16a085", "#c0392b",
    "#2980b9", "#27ae60", "#8e44ad", "#f1c40f", "#7f8c8d",
]


def _color(i: int) -> str:
    return PALETTE[i % len(PALETTE)]


def _short_model(m: str, n: int = 24) -> str:
    return m if len(m) <= n else m[: n - 1] + "…"


class BillChart:
    """Tk 计费监控：1 秒轮询 llm计费.json。"""

    VIEWS = [("按调用", "call"), ("每日", "day"),
             ("每周", "week"), ("每月", "month")]

    def __init__(self, ws: str):
        import tkinter as tk
        from tkinter import font as tkfont
        self.tk = tk
        self.ws = ws
        self.path = os.path.join(ws, "llm计费.json")

        self.root = tk.Tk()
        self.root.title("LLM 计费监控 · showtagger")
        self.root.geometry("1040x640")
        self.root.minsize(820, 480)
        self.root.configure(bg=BG_COLOR)

        try:
            self.f_title = tkfont.Font(family="Microsoft YaHei UI", size=11, weight="bold")
            self.f_body = tkfont.Font(family="Microsoft YaHei UI", size=10)
            self.f_small = tkfont.Font(family="Microsoft YaHei UI", size=9)
        except Exception:
            self.f_title = tkfont.Font(size=11, weight="bold")
            self.f_body = tkfont.Font(size=10)
            self.f_small = tkfont.Font(size=9)

        self.var_info = tk.StringVar()
        self.var_status = tk.StringVar(value="等待数据…")
        self._last_stat = None
        self._last_data = None
        # 折线
        self._series = []
        self._y_max = 1
        self._total_in = 0
        self._total_out = 0
        self._n = 0
        self._in_tokens = []
        self._out_tokens = []
        self._all_times = []
        self._meta = []
        # 热力图
        self.view_mode = "call"
        self._grid = {}
        self._n_rows = 1
        self._n_cols = 0
        self._x_axis = []     # [(col, label_text)]
        self._y_labels = []
        self._x_title = ""
        self._grid_max = 1
        self._cell_meta = []  # [(x1,y1,x2,y2,info)]

        self._build()
        self._tick(force=True)
        self.root.after(1000, self._schedule)

    # ---------- 部件 ----------
    def _build(self):
        tk = self.tk
        top = tk.Frame(self.root, bg="#161b22")
        top.pack(side="top", fill="x")
        tk.Label(top, text="📈 LLM 计费监控", font=self.f_title,
                 bg="#161b22", fg=TEXT_COLOR).pack(side="left", padx=10, pady=6)
        tk.Label(top, textvariable=self.var_info, font=self.f_small,
                 bg="#161b22", fg=TEXT_DIM, anchor="w").pack(side="left", padx=4)

        bar = tk.Frame(self.root, bg="#161b22")
        bar.pack(side="top", fill="x")
        tk.Label(bar, text="视图：", font=self.f_small, bg="#161b22",
                 fg=TEXT_DIM).pack(side="left", padx=(10, 2), pady=4)
        self.view_btns = {}
        for (label, mode) in self.VIEWS:
            b = tk.Button(bar, text=label, font=self.f_small, relief="flat",
                          bg="#21262d" if mode == self.view_mode else "#161b22",
                          fg=TEXT_COLOR, activebackground="#30363d",
                          activeforeground=TEXT_COLOR,
                          padx=10, pady=2,
                          command=lambda m=mode: self.set_view(m))
            b.pack(side="left", padx=2, pady=4)
            self.view_btns[mode] = b
        self.level_legend = tk.Frame(bar, bg="#161b22")
        self.level_legend.pack(side="left", padx=16)
        self._build_level_legend()

        self.canvas = tk.Canvas(self.root, bg=BG_COLOR, highlightthickness=0)
        self.canvas.pack(side="top", fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self._redraw())
        self.canvas.bind("<Motion>", self._on_motion)

        self.legend = tk.Frame(self.root, bg="#161b22")
        self.legend.pack(side="top", fill="x")

        self.status = tk.Label(self.root, textvariable=self.var_status, font=self.f_small,
                               bg="#0a0e14", fg=TEXT_COLOR, anchor="w", padx=10, pady=4)
        self.status.pack(side="bottom", fill="x")

    def _build_level_legend(self):
        tk = self.tk
        for child in self.level_legend.winfo_children():
            child.destroy()
        if self.view_mode == "call":
            self.level_legend.pack_forget()
            return
        self.level_legend.pack(side="left", padx=16)
        tk.Label(self.level_legend, text="用量等级：", font=self.f_small,
                 bg="#161b22", fg=TEXT_DIM).pack(side="left")
        tk.Label(self.level_legend, text="无", font=self.f_small,
                 bg="#161b22", fg=TEXT_DIM).pack(side="left", padx=(8, 1))
        tk.Label(self.level_legend, text="  ", bg=NONE_COLOR, width=2, height=1).pack(
            side="left", padx=2)
        for i, (color, label) in enumerate(zip(HEATMAP_COLORS, LEVEL_LABELS)):
            tk.Label(self.level_legend, text="  ", bg=color, width=2, height=1).pack(
                side="left", padx=(8, 1))
            tk.Label(self.level_legend, text=label, font=self.f_small,
                     bg="#161b22", fg=TEXT_COLOR).pack(side="left", padx=(0, 4))

    def set_view(self, mode: str):
        if mode == self.view_mode:
            return
        self.view_mode = mode
        for m, b in self.view_btns.items():
            b.configure(bg="#21262d" if m == mode else "#161b22")
        self._build_level_legend()
        self._render(self._last_data)

    # ---------- 数据 ----------
    def _schedule(self):
        try:
            self._tick()
        finally:
            self.root.after(1000, self._schedule)

    def _tick(self, force=False):
        try:
            stat = os.stat(self.path)
            key = (stat.st_mtime_ns, stat.st_size)
            if not force and key == self._last_stat:
                return
            self._last_stat = key
        except OSError:
            if not force and self._last_stat is None:
                return
            self._last_stat = None
            self.var_info.set(f"未找到 {self.path}")
            self.var_status.set("等待 llm计费.json 生成…")
            self._redraw_empty()
            return
        data = load_bill(self.path)
        self._last_data = data
        self._render(data)

    def _render(self, data):
        if data is None:
            self.var_info.set(f"文件为空：{self.path}")
            self.var_status.set("尚无计费记录")
            self._redraw_empty()
            return
        records = data.get("records") or []
        # 折线（始终计算）
        self._series, self._y_max = prepare_series(records)
        self._total_in = sum(int(r.get("prompt_tokens", 0) or 0) for r in records)
        self._total_out = sum(int(r.get("completion_tokens", 0) or 0) for r in records)
        self._n = len(records)
        self._in_tokens = [int(r.get("prompt_tokens", 0) or 0) for r in records]
        self._out_tokens = [int(r.get("completion_tokens", 0) or 0) for r in records]
        self._all_times = [str(r.get("time", "")) for r in records]
        # 热力图
        if self.view_mode != "call":
            (self._grid, self._n_rows, self._n_cols,
             self._x_axis, self._y_labels, self._x_title,
             self._grid_max, _, _) = aggregate_grid(records, self.view_mode)
        last_time = records[-1].get("time", "") if records else ""
        self.var_info.set(
            f"{os.path.basename(self.path)} · {self._n} 次调用 · "
            f"输入 {self._total_in:,} / 输出 {self._total_out:,} tokens · "
            f"最新 {last_time}")
        self._build_legend()
        self._redraw()

    def _redraw_empty(self):
        c = self.canvas
        c.delete("all")
        w = max(c.winfo_width(), 200)
        h = max(c.winfo_height(), 200)
        c.create_text(w / 2, h / 2, text="⏳ 等待计费数据…",
                      font=self.f_body, fill=TEXT_DIM)

    # ---------- 图例 ----------
    def _build_legend(self):
        for child in self.legend.winfo_children():
            child.destroy()
        tk = self.tk
        if self.view_mode == "call":
            if not self._series:
                tk.Label(self.legend, text="（暂无数据）", font=self.f_small,
                         bg="#161b22", fg=TEXT_DIM, padx=8).pack(side="left")
                return
            for s in self._series:
                total = sum(p[1] for p in s["points"])
                color = _color(s["color_idx"])
                tk.Label(self.legend, text="  ", bg=color, width=2, height=1).pack(
                    side="left", padx=(12, 2), pady=4)
                tk.Label(self.legend, font=self.f_small, bg="#161b22", fg=TEXT_COLOR,
                         text=f"{_short_model(s['model'])} · {len(s['points'])} 次 · {total:,} t"
                         ).pack(side="left")
        else:
            grain_name = {"day": "日", "week": "周", "month": "月"}[self.view_mode]
            tk.Label(self.legend, font=self.f_small, bg="#161b22", fg=TEXT_COLOR,
                     text=f"  Token 活动 · 按{grain_name}聚合 · "
                          f"共 {len(self._grid)} 个{grain_name}有数据 · "
                          f"合计 {sum(g['total'] for g in self._grid.values()):,} tokens"
                     ).pack(side="left", padx=10, pady=6)

    # ---------- 绘图 ----------
    def _redraw(self):
        c = self.canvas
        c.delete("all")
        if self.view_mode == "call":
            self._redraw_call()
        else:
            self._redraw_heatmap()

    def _redraw_call(self):
        c = self.canvas
        if not self._series:
            return
        W = c.winfo_width()
        H = c.winfo_height()
        if W < 120 or H < 120:
            return
        L, R, T, B = 66, 30, 34, 48
        pw, ph = W - L - R, H - T - B
        y_max = self._y_max * 1.08
        for k in range(6):
            val = y_max * k / 5
            y = T + ph * (1 - k / 5)
            c.create_line(L, y, W - R, y, fill="#1f2937")
            c.create_text(L - 8, y, text=f"{int(val):,}", anchor="e",
                          font=self.f_small, fill=TEXT_DIM)
        n = self._n
        ticks = min(n, 8)
        for k in range(ticks):
            idx = 1 + int(k * (n - 1) / (ticks - 1)) if ticks > 1 else 1
            x = L + pw * (idx - 1) / (n - 1) if n > 1 else L + pw / 2
            t = (_parse_time(self._all_times[min(idx - 1, len(self._all_times) - 1)])
                 if self._all_times else None)
            label = t.strftime("%H:%M:%S") if t else f"#{idx}"
            c.create_line(x, T, x, T + ph, fill="#161e2a")
            c.create_text(x, T + ph + 14, text=label, font=self.f_small, fill=TEXT_DIM)
        c.create_text(L + pw / 2, H - 6, text="时间（第 N 次调用）→",
                      font=self.f_small, fill=TEXT_DIM)
        c.create_text(L - 44, T + ph / 2, text="tokens", angle=90,
                      font=self.f_small, fill=TEXT_DIM)
        self._meta = []
        for s in self._series:
            color = _color(s["color_idx"])
            pts = s["points"]
            coords = []
            for (idx, total, tstr) in pts:
                x = L + pw * (idx - 1) / (n - 1) if n > 1 else L + pw / 2
                y = T + ph * (1 - total / y_max)
                coords.extend((x, y))
                self._meta.append((x, y, self._point_info(idx, total, tstr, s["model"])))
            if len(pts) >= 2:
                c.create_line(coords, fill=color, width=2, smooth=True)
            for (x, y) in zip(coords[::2], coords[1::2]):
                c.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline="")

    def _redraw_heatmap(self):
        c = self.canvas
        W = c.winfo_width()
        H = c.winfo_height()
        if W < 200 or H < 200 or self._n_cols <= 0:
            return
        n_rows, n_cols = self._n_rows, self._n_cols
        # 布局：左侧 y 标签区，右侧绘图区，底部 x 标签
        y_label_w = 36 if n_rows <= 7 else 44
        x_label_h = 22
        top_pad = 30
        L, T, R, B = y_label_w, top_pad, 14, x_label_h
        pw, ph = W - L - R, H - T - B
        if pw < 40 or ph < 40:
            return
        # 单元格大小（含 gap）
        gap = 3
        cw = (pw - gap * (n_cols - 1)) / n_cols if n_cols > 0 else 0
        ch = (ph - gap * (n_rows - 1)) / n_rows if n_rows > 0 else 0
        cell_size = max(min(cw, ch), 4)
        # 居中
        total_w = n_cols * cell_size + (n_cols - 1) * gap
        total_h = n_rows * cell_size + (n_rows - 1) * gap
        x_off = L + (pw - total_w) / 2
        y_off = T + (ph - total_h) / 2
        # y 标签
        for r in range(n_rows):
            label = self._y_labels[r] if r < len(self._y_labels) else f"R{r}"
            yc = y_off + r * (cell_size + gap) + cell_size / 2
            c.create_text(L - 6, yc, text=label, anchor="e",
                          font=self.f_small, fill=TEXT_DIM)
        # x 标签（按 col 位置对齐）
        for (col, lbl) in self._x_axis:
            if 0 <= col < n_cols:
                xc = x_off + col * (cell_size + gap) + cell_size / 2
                c.create_text(xc, T + total_h + 14, text=lbl,
                              font=self.f_small, fill=TEXT_DIM)
        # 标题"按月"等
        c.create_text(L, T - 12, text=f"Token 活动 · 按{self._x_title}聚合 →",
                      font=self.f_small, fill=TEXT_DIM, anchor="w")
        # 画方格
        self._cell_meta = []
        for r in range(n_rows):
            for col in range(n_cols):
                x1 = x_off + col * (cell_size + gap)
                y1 = y_off + r * (cell_size + gap)
                x2 = x1 + cell_size
                y2 = y1 + cell_size
                cell = self._grid.get((r, col))
                if cell and cell["total"] > 0:
                    color = level_color(cell["total"], self._grid_max)
                    outline = ""
                else:
                    color = NONE_COLOR
                    outline = GRID_LINE
                # 圆角矩形（用 create_rectangle，rounded 用 _round_rect 自己画太复杂，简化为直角）
                kw = {"fill": color, "outline": outline, "width": 0 if cell and cell["total"] > 0 else 1}
                c.create_rectangle(x1, y1, x2, y2, **kw)
                if cell:
                    lvl = level_index(cell["total"], self._grid_max)
                    lvl_lbl = LEVEL_LABELS[lvl] if lvl >= 0 else "无"
                    info = (f"{cell['label']} · {cell['n']} 次调用 · "
                            f"输入 {cell['in']:,} / 输出 {cell['out']:,} · "
                            f"合计 {cell['total']:,} t · 等级 {lvl_lbl}")
                    self._cell_meta.append((x1, y1, x2, y2, info))

    def _point_info(self, idx, total, tstr, model):
        return (f"#{idx} {tstr} · {model} · 总 {total:,} tokens · "
                f"输入 {self._meta_in(idx):,} / 输出 {self._meta_out(idx):,}")

    def _meta_in(self, idx):
        return self._in_tokens[idx - 1] if hasattr(self, "_in_tokens") and 0 < idx <= len(self._in_tokens) else 0

    def _meta_out(self, idx):
        return self._out_tokens[idx - 1] if hasattr(self, "_out_tokens") and 0 < idx <= len(self._out_tokens) else 0

    # ---------- 悬停 ----------
    def _on_motion(self, event):
        x, y = event.x, event.y
        if self.view_mode == "call":
            best = None
            for (px, py, info) in self._meta:
                d2 = (px - x) ** 2 + (py - y) ** 2
                if d2 <= 196 and (best is None or d2 < best[0]):
                    best = (d2, info)
            self.var_status.set(best[1] if best else
                                f"{self._n} 次调用 · 输入 {self._total_in:,} / 输出 {self._total_out:,} tokens")
        else:
            for (x1, y1, x2, y2, info) in self._cell_meta:
                if x1 <= x <= x2 and y1 <= y <= y2:
                    self.var_status.set(info)
                    return
            self.var_status.set(
                f"共 {len(self._grid)} 个{self._x_title or '格'}有数据 · "
                f"合计 {sum(g['total'] for g in self._grid.values()):,} tokens · "
                f"输入 {self._total_in:,} / 输出 {self._total_out:,}")


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    ws = resolve_workspace(argv[0] if argv else None)
    try:
        app = BillChart(ws)
    except Exception as e:
        print(f"❌ 打开监控窗口失败：{e}\n"
              f"   提示：需要 Tkinter（python -m tkinter 可验证）。", file=sys.stderr)
        return 1
    app.root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
