"""pan-accelerator 图形界面主窗口（Material 3 Expressive）。

启动： python -m pan_accel.gui
"""
from __future__ import annotations

import os
import queue
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

from .. import __version__
from .runner import TaskRunner
from .tasks import Task, TaskKind, TaskState
from .tokens import SEEDS, make_theme
from .widgets import (TYPE_SCALE, Fonts, NavRail, RoundedButton,
                      SegmentedControl, Slider, Switch, TextField, round_rect,
                      ease_out)


# ---------------------------------------------------------------------------
# 任务卡片（用绝对定位的 Canvas 绘制，严格分区避免重叠）
# ---------------------------------------------------------------------------

CARD_H = 142
PAD = 20
BADGE = 44


class TaskCard(tk.Canvas):
    """任务行卡：左侧徽标、中间信息、右侧操作，三段式静态布局。"""

    def __init__(self, master, theme, task: Task, *, on_pause, on_remove,
                 on_open, fonts: Fonts):
        super().__init__(master, bg=theme.c("surface_container_low"),
                         highlightthickness=0, bd=0, height=CARD_H)
        self.theme = theme
        self.task = task
        self.fonts = fonts
        self.on_pause = on_pause
        self.on_remove = on_remove
        self.on_open = on_open
        self._hover = False
        self._hover_btn: str | None = None
        self.bind("<Configure>", lambda e: self.render())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def set_theme(self, theme):
        self.theme = theme
        self.configure(bg=theme.c("surface_container_low"))
        self.render()

    # ------------------------------------------------------------------ 交互
    def _buttons(self) -> list[tuple[str, tuple[float, float, float, float], object]]:
        """返回可点击区域（圆形按钮的包围盒）。"""
        w = self.winfo_width()
        btn_r = 17
        cx = w - PAD - btn_r
        out = []
        t = self.task
        if t.active:
            out.append(("pause", (cx - btn_r, 22 - btn_r, cx + btn_r, 22 + btn_r),
                        lambda: self.on_pause(t)))
        elif t.state in (TaskState.PAUSED, TaskState.FAILED):
            out.append(("resume", (cx - btn_r, 22 - btn_r, cx + btn_r, 22 + btn_r),
                        lambda: self.on_pause(t)))
        if t.state == TaskState.DONE and t.output:
            ox = cx - 44
            out.append(("open", (ox - btn_r, 22 - btn_r, ox + btn_r, 22 + btn_r),
                        lambda: self.on_open(t)))
        rx = cx
        ry = CARD_H - 30
        out.append(("remove", (rx - 14, ry - 14, rx + 14, ry + 14),
                    lambda: self.on_remove(t)))
        return out

    def _on_motion(self, e):
        key = None
        for name, box, _ in self._buttons():
            if box[0] <= e.x <= box[2] and box[1] <= e.y <= box[3]:
                key = name
                break
        if key != self._hover_btn:
            self._hover_btn = key
            self.configure(cursor="hand2" if key else "arrow")
            self.render()

    def _on_leave(self, _):
        if self._hover_btn:
            self._hover_btn = None
            self.render()

    def _on_click(self, e):
        for name, box, cb in self._buttons():
            if box[0] <= e.x <= box[2] and box[1] <= e.y <= box[3]:
                cb()
                return

    # ------------------------------------------------------------------ 绘制
    def render(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 40 or h <= 20:
            return
        t, task, f = self.theme, self.task, self.fonts

        # 卡片底板
        bg_role = "surface_container" if self._hover else "surface_container_low"
        round_rect(self, 0, 0, w - 1, h - 1, t.radius_xl, fill=t.c(bg_role), outline="")

        # 右侧操作区宽度（留出按钮列）
        actions_w = 96 if task.state == TaskState.DONE else 52
        body_right = w - PAD - actions_w

        # ---- 徽标 ----
        icon, role = self._icon_and_role()
        round_rect(self, PAD, 20, PAD + BADGE, 20 + BADGE, t.radius_md,
                   fill=t.c(role), outline="")
        self.create_text(PAD + BADGE / 2, 20 + BADGE / 2, text=icon,
                         fill=t.c("on_tertiary_container"), font=f.get(19))

        # ---- 标题 + 状态 chip ----
        tx = PAD + BADGE + 14
        state_text, chip_role, chip_fg = self._state_chip()
        chip_font = f.get(TYPE_SCALE["label_sm"] + 1, "bold")
        chip_w = chip_font.measure(state_text) + 24
        chip_h = 24
        chip_x = body_right - chip_w
        title_right = chip_x - 12
        title = self._ellipsize(task.name, title_right - tx, f.get(TYPE_SCALE["title"], "bold"))
        self.create_text(tx, 20, text=title, anchor="nw",
                         fill=t.c("on_surface"), font=f.get(TYPE_SCALE["title"], "bold"))
        round_rect(self, chip_x, 20, chip_x + chip_w, 20 + chip_h, chip_h / 2,
                   fill=t.c(chip_role), outline="")
        self.create_text(chip_x + chip_w / 2, 20 + chip_h / 2, text=state_text,
                         fill=t.c(chip_fg), font=chip_font)

        # ---- 进度条 ----
        bar_y = 54
        bar_h = 8
        bar_x2 = body_right
        round_rect(self, tx, bar_y, bar_x2, bar_y + bar_h, bar_h / 2,
                   fill=t.c("surface_container_highest"), outline="")
        p = task.progress
        if p > 0.002:
            fill_role = "primary"
            if task.state == TaskState.FAILED:
                fill_role = "error"
            elif task.state == TaskState.DONE:
                fill_role = "success"
            end = tx + max(bar_h, (bar_x2 - tx) * p)
            round_rect(self, tx, bar_y, end, bar_y + bar_h, bar_h / 2,
                       fill=t.c(fill_role), outline="")

        # ---- 指标行（分列，互不挤占） ----
        my = bar_y + bar_h + 10
        metrics_font = f.get(TYPE_SCALE["body"])
        bold_font = f.get(TYPE_SCALE["body"], "bold")
        col = tx
        size_txt = task.human_size(task.total_bytes) if task.total_bytes else "—"
        self.create_text(col, my, text=size_txt, anchor="nw",
                         fill=t.c("on_surface_variant"), font=metrics_font)
        if task.total_bytes:
            self.create_text(col + 96, my, text=f"{p * 100:.0f}%", anchor="nw",
                             fill=t.c("on_surface"), font=bold_font)

        if task.state in (TaskState.DOWNLOADING, TaskState.PROBING):
            self.create_text(col + 152, my, text=task.human_speed(), anchor="nw",
                             fill=t.c("primary"), font=bold_font)
            self.create_text(col + 268, my, text=f"剩余 {task.human_eta()}", anchor="nw",
                             fill=t.c("on_surface_variant"), font=metrics_font)
        elif task.state == TaskState.DONE:
            label = "✓ 校验通过" if task.verified else "✓ 已完成"
            self.create_text(col + 152, my, text=label, anchor="nw",
                             fill=t.c("success"), font=bold_font)
        elif task.state == TaskState.FAILED:
            err = self._ellipsize(task.error, body_right - col - 152,
                                  f.get(TYPE_SCALE["caption"] + 1))
            self.create_text(col + 152, my, text=err, anchor="nw",
                             fill=t.c("error"), font=f.get(TYPE_SCALE["caption"] + 1))
        elif task.state == TaskState.PAUSED:
            self.create_text(col + 152, my, text="已暂停 · 可继续", anchor="nw",
                             fill=t.c("on_surface_variant"), font=metrics_font)
        elif task.state == TaskState.VERIFYING:
            self.create_text(col + 152, my, text="正在校验哈希…", anchor="nw",
                             fill=t.c("tertiary"), font=metrics_font)

        # ---- 分块热图（独立一行，位于指标行下方） ----
        if task.chunk_total > 1:
            self._draw_heat(tx, my + 22, min(420, bar_x2 - tx), task)

        # ---- 底部：来源（等宽小字，靠左；右侧留给操作按钮） ----
        src = self._ellipsize(task.source, body_right - tx - 8, f.mono_get(10))
        self.create_text(tx, h - 26, text=src, anchor="nw",
                         fill=t.c("outline"), font=f.mono_get(10))

        # ---- 右侧操作按钮 ----
        for name, box, _ in self._buttons():
            self._draw_action(name, box, t, f)

    def _draw_action(self, name, box, t, f):
        x1, y1, x2, y2 = box
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        r = (x2 - x1) / 2
        hovered = self._hover_btn == name
        glyph = {"pause": "❚❚", "resume": "▶", "open": "▸", "remove": "✕"}[name]
        fill = t.c("surface_container_high") if hovered else t.c("surface_container")
        fg = {"remove": t.c("error")}.get(name, t.c("on_surface_variant"))
        if hovered and name == "remove":
            fill = t.c("error_container")
            fg = t.c("on_error_container")
        round_rect(self, x1, y1, x2, y2, r, fill=fill, outline="")
        self.create_text(cx, cy, text=glyph, fill=fg,
                         font=f.get(11 if name == "pause" else 13))

    def _draw_heat(self, x, y, width, task: Task):
        t = self.theme
        n = task.chunk_total
        gap = 2
        cell = max(2, min(6, (width - gap * (n - 1)) / max(1, n)))
        for i in range(n):
            done = i < task.chunk_done
            color = t.c("primary") if done else t.c("surface_container_highest")
            cx = x + i * (cell + gap)
            round_rect(self, cx, y, cx + cell, y + 4, 2, fill=color, outline="")

    # ------------------------------------------------------------------ 辅助
    def _icon_and_role(self):
        """用几何符号而非 emoji：YaHei 等中文字体缺彩色 emoji 字形。"""
        kind = self.task.kind
        if kind == TaskKind.BT:
            return "⛓", "tertiary_container"
        if kind == TaskKind.THUNDER:
            return "⚡", "tertiary_container"
        return "↓", "secondary_container"

    def _state_chip(self):
        s = self.task.state
        if s == TaskState.DONE:
            # 已完成：绿底 + 深色字，保证可读
            return "已完成", "success", "on_success"
        if s == TaskState.FAILED:
            return "失败", "error_container", "on_error_container"
        if s == TaskState.PAUSED:
            return "已暂停", "surface_container_highest", "on_surface_variant"
        if s == TaskState.VERIFYING:
            return "校验中", "tertiary_container", "on_tertiary_container"
        if s == TaskState.QUEUED:
            return "排队中", "surface_container_highest", "on_surface_variant"
        if s == TaskState.PROBING:
            return "探测中", "secondary_container", "on_secondary_container"
        return "下载中", "secondary_container", "on_secondary_container"

    def _ellipsize(self, text: str, width: float, font) -> str:
        if not text:
            return ""
        if width <= 12:
            return ""
        if font.measure(text) <= width:
            return text
        ell = "…"
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi) // 2
            if font.measure(text[:mid] + ell) <= width:
                lo = mid + 1
            else:
                hi = mid
        return text[:max(0, lo - 1)] + ell


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------

NAV_ITEMS = [("↓", "下载"), ("≡", "统计"), ("◐", "外观"), ("⚙", "设置")]


class MainWindow(tk.Tk):
    def __init__(self, theme=None, seed="#3D7BEA", dark=True):
        super().__init__()
        self.title(f"pan-accelerator {__version__}")
        self.geometry("1200x800")
        self.minsize(1040, 700)

        self.seed = seed
        self.dark = dark
        self.theme = theme or make_theme(dark=dark, seed=seed)
        self.fonts = Fonts(self, scale=1.0)

        self.tasks: list[Task] = []
        self.runners: dict[int, TaskRunner] = {}
        self.cards: dict[int, TaskCard] = {}
        self._next_id = 1
        self._ui_queue: queue.Queue = queue.Queue()
        self._page = 0
        self._out_dir = str(Path.home() / "Downloads")
        self._default_threads = 8
        self._open_after = False

        self.configure(bg=self.theme.c("surface"))
        self._build()
        self.after(16, self._pump)

    # ------------------------------------------------------------- 构建骨架
    def _build(self):
        t = self.theme
        root = tk.Frame(self, bg=t.c("surface"))
        root.pack(fill="both", expand=True)

        # 顶部标题栏
        header = tk.Frame(root, bg=t.c("surface"), height=96)
        header.pack(fill="x", padx=28, pady=(14, 0))
        header.pack_propagate(False)
        self.header = header
        self.title_canvas = tk.Canvas(header, bg=t.c("surface"),
                                      highlightthickness=0, bd=0, height=76)
        self.title_canvas.pack(fill="both", expand=True)
        self.title_canvas.bind("<Configure>", lambda e: self._draw_header())

        body = tk.Frame(root, bg=t.c("surface"))
        body.pack(fill="both", expand=True, padx=(12, 24), pady=(4, 18))
        self.body = body

        self.nav = NavRail(body, t, NAV_ITEMS, index=0,
                           command=self._switch_page, width=96)
        self.nav.pack(side="left", fill="y", padx=(0, 18))

        self.content = tk.Frame(body, bg=t.c("surface"))
        self.content.pack(side="left", fill="both", expand=True)

        self.pages = []
        for builder in (self._page_download, self._page_stats,
                        self._page_appearance, self._page_settings):
            frame = tk.Frame(self.content, bg=t.c("surface"))
            self.pages.append(frame)
            builder(frame)
        self.pages[0].pack(fill="both", expand=True)
        self._draw_header()

    def _draw_header(self):
        c = self.title_canvas
        c.delete("all")
        t = self.theme
        w = c.winfo_width()
        c.create_text(4, 6, text="下载中心", anchor="nw",
                      fill=t.c("on_surface"), font=self.fonts.get(27, "bold"))
        c.create_text(4, 46, text="多线程直链加速 · BT 磁力链 · 断点续传",
                      anchor="nw", fill=t.c("on_surface_variant"),
                      font=self.fonts.get(TYPE_SCALE["body"]))
        if not hasattr(self, "_theme_switch"):
            self._theme_switch = Switch(c, t, value=self.dark,
                                        command=self._on_theme_switch)
        c.create_window(w - 76, 8, window=self._theme_switch, anchor="nw",
                        width=56, height=30)
        c.create_text(w - 86, 15, text="深色", anchor="ne",
                      fill=t.c("on_surface_variant"),
                      font=self.fonts.get(TYPE_SCALE["label_sm"] + 1))

    # --------------------------------------------------------------- 下载页
    def _page_download(self, parent):
        t = self.theme
        wrap = tk.Frame(parent, bg=t.c("surface"))
        wrap.pack(fill="both", expand=True)

        # ---------- 新建任务卡 ----------
        composer = tk.Canvas(wrap, bg=t.c("surface_container"), height=200,
                             highlightthickness=0, bd=0)
        composer.pack(fill="x", pady=(0, 18))
        composer.bind("<Configure>", lambda e: self._layout_composer())
        self.composer = composer

        # 输入框（叠加在 composer 上）
        self.url_field = TextField(composer, t,
                                   placeholder="粘贴下载直链，或整段浏览器「Copy as cURL」命令…",
                                   height=56, role="surface_container_high")
        self.url_field.configure(bg=t.c("surface_container"))

        self.start_btn = RoundedButton(composer, t, "开始下载",
                                       command=self._start_task, variant="filled",
                                       height=52, icon="⬇", font_size=13)

        self.mode_seg = SegmentedControl(composer, t, ["直链 / 迅雷", "BT 磁力链"],
                                         index=0, command=self._switch_mode,
                                         height=38)

        self._composer_widgets = [
            (self.url_field, "input"),
            (self.mode_seg, "seg"),
            (self.start_btn, "start"),
        ]
        self._build_composer_opts(t)

        # ---------- 队列标题 ----------
        head = tk.Frame(wrap, bg=t.c("surface"))
        head.pack(fill="x", pady=(0, 10))
        self.count_label = tk.Label(head, text="队列 · 0 个任务", bg=t.c("surface"),
                                    fg=t.c("on_surface_variant"),
                                    font=self.fonts.get(TYPE_SCALE["label"] + 1))
        self.count_label.pack(side="left")
        self.clear_btn = RoundedButton(head, t, "清除已完成", command=self._clear_done,
                                       variant="text", height=34, font_size=12)
        self.clear_btn.pack(side="right")

        # ---------- 列表 ----------
        list_bg = tk.Frame(wrap, bg=t.c("surface"))
        list_bg.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(list_bg, bg=t.c("surface"), highlightthickness=0, bd=0)
        self.scroll = tk.Scrollbar(list_bg, orient="vertical",
                                   command=self.canvas.yview, width=10)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.task_frame = tk.Frame(self.canvas, bg=t.c("surface"))
        self._task_window = self.canvas.create_window((0, 0), window=self.task_frame,
                                                      anchor="nw")
        self.task_frame.bind("<Configure>", self._on_task_frame_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

        self.empty_hint = tk.Label(
            self.task_frame, text="还没有任务 — 在上方粘贴链接即可开始",
            bg=t.c("surface"), fg=t.c("on_surface_variant"),
            font=self.fonts.get(13))
        self.empty_hint.pack(pady=70)

    def _build_composer_opts(self, t):
        """composer 底部的选项行（主题切换时需重建）。"""
        opts = tk.Frame(self.composer, bg=t.c("surface_container"))
        self.composer_opts = opts

        tk.Label(opts, text="保存到", bg=t.c("surface_container"),
                 fg=t.c("on_surface_variant"),
                 font=self.fonts.get(TYPE_SCALE["label_sm"] + 1)).pack(side="left",
                                                                       padx=(0, 8))
        self.out_label = tk.Label(opts, text=self._short_path(self._out_dir),
                                  bg=t.c("surface_container"), fg=t.c("on_surface"),
                                  font=self.fonts.get(TYPE_SCALE["label"] + 1))
        self.out_label.pack(side="left", padx=(0, 8))
        self.pick_btn = RoundedButton(opts, t, "更改", command=self._pick_dir,
                                      variant="text", height=34, font_size=12)
        self.pick_btn.pack(side="left", padx=(0, 24))

        tk.Label(opts, text="并发", bg=t.c("surface_container"),
                 fg=t.c("on_surface_variant"),
                 font=self.fonts.get(TYPE_SCALE["label_sm"] + 1)).pack(side="left",
                                                                       padx=(0, 6))
        self.thread_slider = Slider(opts, t, minimum=1, maximum=32,
                                    value=self._default_threads,
                                    command=self._on_threads, width=150, height=34)
        self.thread_slider.configure(bg=t.c("surface_container"))
        self.thread_slider.pack(side="left", padx=(0, 6))
        self.thread_value = tk.Label(opts, text=str(self._default_threads),
                                     bg=t.c("surface_container"), fg=t.c("primary"),
                                     font=self.fonts.get(TYPE_SCALE["label"] + 1, "bold"),
                                     width=3, anchor="w")
        self.thread_value.pack(side="left", padx=(0, 22))

        tk.Label(opts, text="MD5", bg=t.c("surface_container"),
                 fg=t.c("on_surface_variant"),
                 font=self.fonts.get(TYPE_SCALE["label_sm"] + 1)).pack(side="left",
                                                                       padx=(0, 8))
        self.md5_field = TextField(opts, t, placeholder="可选，粘贴后自动校验",
                                   height=38, role="surface_container_high")
        self.md5_field.configure(bg=t.c("surface_container"))
        self.md5_field.pack(side="left", fill="x", expand=True, padx=(0, 4))

    def _layout_composer(self):
        """在 composer 画布上按尺寸摆放子控件。"""
        c = self.composer
        t = self.theme
        w, h = c.winfo_width(), c.winfo_height()
        if w <= 10:
            return
        c.delete("all")
        round_rect(c, 0, 0, w - 1, h - 1, t.radius_2xl,
                   fill=t.c("surface_container"), outline="")

        pad = 20
        inner = w - pad * 2
        # 标题行
        c.create_text(pad, 18, text="新建下载", anchor="nw",
                      fill=t.c("on_surface"), font=self.fonts.get(15, "bold"))
        # ---- 模式切换放标题行右侧 ----
        seg_w = 200
        self._place(self.mode_seg, w - pad - seg_w, 12, seg_w, 38)

        # ---- 输入框整行 ----
        self._place(self.url_field, pad, 58, inner, 58)

        # ---- 底部操作行：选项在左，开始按钮在右 ----
        btn_w = 148
        btn_h = 50
        row_y = 132
        self._place(self.start_btn, w - pad - btn_w, row_y, btn_w, btn_h)

        opts_w = inner - btn_w - 16
        self.composer_opts.configure(bg=t.c("surface_container"))
        self._place(self.composer_opts, pad, row_y, opts_w, btn_h)

    def _place(self, widget, x, y, width, height):
        """把普通控件放进 canvas 坐标系。"""
        key = f"_win_{id(widget)}"
        win = getattr(self, key, None)
        if win is None:
            win = self.composer.create_window(x, y, window=widget, anchor="nw",
                                              width=width, height=height)
            setattr(self, key, win)
        else:
            self.composer.coords(win, x, y)
            self.composer.itemconfigure(win, width=width, height=height)
        self.composer.tag_raise(win)
    def _page_stats(self, parent):
        t = self.theme
        wrap = tk.Frame(parent, bg=t.c("surface"))
        wrap.pack(fill="both", expand=True)
        self.stats_canvas = tk.Canvas(wrap, bg=t.c("surface"),
                                      highlightthickness=0, bd=0)
        self.stats_canvas.pack(fill="both", expand=True)
        self.stats_canvas.bind("<Configure>", lambda e: self._draw_stats())
        self._stats_hint = tk.Label(wrap, text="", bg=t.c("surface"))

    def _draw_stats(self):
        c = self.stats_canvas
        c.delete("all")
        t = self.theme
        w = c.winfo_width()
        if w <= 60:
            return
        total = len(self.tasks)
        done = sum(1 for x in self.tasks if x.state == TaskState.DONE)
        fail = sum(1 for x in self.tasks if x.state == TaskState.FAILED)
        active = sum(1 for x in self.tasks if x.active)
        bytes_total = sum(x.total_bytes for x in self.tasks)
        bytes_done = sum(x.done_bytes for x in self.tasks)

        c.create_text(6, 6, text="总览", anchor="nw", fill=t.c("on_surface"),
                      font=self.fonts.get(21, "bold"))

        stats = [("任务总数", str(total), "on_surface"),
                 ("进行中", str(active), "tertiary"),
                 ("已完成", str(done), "success"),
                 ("失败", str(fail), "error")]
        gap = 16
        box_w = (w - 12 - gap * 3) / 4
        x = 6
        for label, value, role in stats:
            round_rect(c, x, 46, x + box_w, 148, t.radius_lg,
                       fill=t.c("surface_container"), outline="")
            c.create_text(x + 20, 66, text=label, anchor="nw",
                          fill=t.c("on_surface_variant"),
                          font=self.fonts.get(TYPE_SCALE["label"] + 1))
            c.create_text(x + 20, 94, text=value, anchor="nw",
                          fill=t.c(role), font=self.fonts.get(34, "bold"))
            x += box_w + gap

        y = 186
        c.create_text(6, y, text="累计进度", anchor="nw", fill=t.c("on_surface"),
                      font=self.fonts.get(TYPE_SCALE["title"], "bold"))
        ratio = (bytes_done / bytes_total) if bytes_total else 0
        bar_y = y + 34
        round_rect(c, 6, bar_y, w - 6, bar_y + 14, 7,
                   fill=t.c("surface_container_highest"), outline="")
        if ratio > 0:
            round_rect(c, 6, bar_y, 6 + (w - 12) * ratio, bar_y + 14, 7,
                       fill=t.c("primary"), outline="")
        c.create_text(6, bar_y + 26,
                      text=f"{self._human(bytes_done)} / {self._human(bytes_total)}",
                      anchor="nw", fill=t.c("on_surface_variant"),
                      font=self.fonts.get(TYPE_SCALE["body"]))

        # 各任务小进度环
        y2 = bar_y + 74
        c.create_text(6, y2, text="任务明细", anchor="nw", fill=t.c("on_surface"),
                      font=self.fonts.get(TYPE_SCALE["title"], "bold"))
        row_y = y2 + 34
        for i, task in enumerate(self.tasks[:6]):
            bx = 6
            by = row_y + i * 34
            name = task.name[:34]
            c.create_text(bx, by, text=name, anchor="nw",
                          fill=t.c("on_surface"), font=self.fonts.get(12))
            bar_x = bx + 260
            bw = max(60, w - bar_x - 120)
            round_rect(c, bar_x, by + 3, bar_x + bw, by + 13, 5,
                       fill=t.c("surface_container_highest"), outline="")
            if task.progress > 0:
                round_rect(c, bar_x, by + 3, bar_x + max(10, bw * task.progress),
                           by + 13, 5, fill=t.c("primary"), outline="")
            c.create_text(bar_x + bw + 12, by, text=f"{task.progress * 100:.0f}%",
                          anchor="nw", fill=t.c("on_surface_variant"),
                          font=self.fonts.get(11))

    # --------------------------------------------------------------- 外观页
    def _page_appearance(self, parent):
        t = self.theme
        wrap = tk.Frame(parent, bg=t.c("surface"))
        wrap.pack(fill="both", expand=True)
        self.appear_canvas = tk.Canvas(wrap, bg=t.c("surface"),
                                       highlightthickness=0, bd=0)
        self.appear_canvas.pack(fill="both", expand=True)
        self.appear_canvas.bind("<Configure>", lambda e: self._draw_appearance())
        self.appear_canvas.bind("<Button-1>", self._on_appear_click)
        self._seed_hot: list[tuple[float, float, float, float, str]] = []

    def _draw_appearance(self):
        c = self.appear_canvas
        c.delete("all")
        t = self.theme
        w = c.winfo_width()
        if w <= 60:
            return
        c.create_text(6, 8, text="主题色", anchor="nw", fill=t.c("on_surface"),
                      font=self.fonts.get(21, "bold"))
        c.create_text(6, 42, text="由种子色生成 Material 3 色调板（动态取色）",
                      anchor="nw", fill=t.c("on_surface_variant"),
                      font=self.fonts.get(TYPE_SCALE["body"]))

        self._seed_hot = []
        x = 6
        for name, hexv in SEEDS.items():
            cy = 118
            r = 30
            c.create_oval(x, cy - r, x + 2 * r, cy + r, fill=hexv, outline="")
            if hexv.lower() == self.seed.lower():
                c.create_oval(x - 5, cy - r - 5, x + 2 * r + 5, cy + r + 5,
                              outline=t.c("primary"), width=3)
            c.create_text(x + r, cy + r + 14, text=name,
                          fill=t.c("on_surface_variant"),
                          font=self.fonts.get(11), anchor="n")
            self._seed_hot.append((x, cy - r, x + 2 * r, cy + r, hexv))
            x += 2 * r + 28

        y = 210
        c.create_text(6, y, text="圆角与形状", anchor="nw", fill=t.c("on_surface"),
                      font=self.fonts.get(TYPE_SCALE["title"], "bold"))
        c.create_text(6, y + 26, text="Expressive 的大圆角阶梯（单位 px）",
                      anchor="nw", fill=t.c("on_surface_variant"),
                      font=self.fonts.get(11))
        bx = 6
        for r in (10, 16, 22, 28, 34):
            size = 76
            top = y + 54
            round_rect(c, bx, top, bx + size, top + size, r,
                       fill=t.c("primary_container"), outline=t.c("outline_variant"))
            c.create_text(bx + size / 2, top + size / 2, text=str(r),
                          fill=t.c("on_primary_container"),
                          font=self.fonts.get(12, "bold"))
            bx += size + 16

        # 色彩角色预览
        y2 = y + 168
        c.create_text(6, y2, text="色彩角色", anchor="nw", fill=t.c("on_surface"),
                      font=self.fonts.get(TYPE_SCALE["title"], "bold"))
        roles = [("primary", "primary"), ("secondary_container", "secondary_container"),
                 ("tertiary_container", "tertiary_container"), ("surface_container", "surface_container"),
                 ("error_container", "error_container")]
        px = 6
        for role, _ in roles:
            cw, ch = 132, 68
            top = y2 + 32
            round_rect(c, px, top, px + cw, top + ch, t.radius_md,
                       fill=t.c(role), outline="")
            fg = "on_primary" if role == "primary" else (
                "on_error_container" if role == "error_container" else
                "on_surface" if role.startswith("surface") else
                "on_" + role.replace("_container", "_container"))
            fg_role = {"primary": "on_primary",
                       "secondary_container": "on_secondary_container",
                       "tertiary_container": "on_tertiary_container",
                       "surface_container": "on_surface",
                       "error_container": "on_error_container"}[role]
            c.create_text(px + 12, top + 14, text=role, anchor="nw",
                          fill=t.c(fg_role), font=self.fonts.get(10))
            c.create_text(px + 12, top + 34, text=t.c(role), anchor="nw",
                          fill=t.c(fg_role), font=self.fonts.get(11, "bold"))
            px += cw + 14

    def _on_appear_click(self, e):
        for x1, y1, x2, y2, hexv in self._seed_hot:
            if x1 - 8 <= e.x <= x2 + 8 and y1 - 8 <= e.y <= y2 + 8:
                self._set_seed(hexv)
                return

    # --------------------------------------------------------------- 设置页
    def _page_settings(self, parent):
        t = self.theme
        wrap = tk.Frame(parent, bg=t.c("surface"))
        wrap.pack(fill="both", expand=True)
        self.settings_canvas = tk.Canvas(wrap, bg=t.c("surface"),
                                         highlightthickness=0, bd=0)
        self.settings_canvas.pack(fill="both", expand=True)
        self.settings_canvas.bind("<Configure>", lambda e: self._draw_settings())
        self.settings_canvas.bind("<Button-1>", self._on_settings_click)
        self._setting_rows = [
            ("深色模式", "跟随 Android 17 的明暗切换", "dark"),
            ("完成后自动打开目录", "下载与校验完成后打开文件管理器", "open_after"),
            ("保留分块状态文件", "中断后可续传，完成时自动清理", "keep_state"),
        ]
        self._setting_state = {"dark": self.dark, "open_after": False, "keep_state": True}
        self._setting_hot: list[tuple[float, float, float, float, str]] = []

    def _draw_settings(self):
        c = self.settings_canvas
        c.delete("all")
        t = self.theme
        w = c.winfo_width()
        if w <= 60:
            return
        c.create_text(6, 8, text="设置", anchor="nw", fill=t.c("on_surface"),
                      font=self.fonts.get(21, "bold"))

        self._setting_hot = []
        y = 56
        for title, desc, key in self._setting_rows:
            row_h = 76
            round_rect(c, 6, y, w - 6, y + row_h, t.radius_lg,
                       fill=t.c("surface_container"), outline="")
            c.create_text(24, y + 16, text=title, anchor="nw",
                          fill=t.c("on_surface"),
                          font=self.fonts.get(TYPE_SCALE["body_lg"] + 1, "bold"))
            c.create_text(24, y + 42, text=desc, anchor="nw",
                          fill=t.c("on_surface_variant"),
                          font=self.fonts.get(TYPE_SCALE["caption"] + 1))
            # 开关
            sw, sh = 56, 30
            sx = w - 24 - sw
            sy = y + (row_h - sh) / 2
            on = self._setting_state.get(key, False)
            track = t.c("primary") if on else t.c("surface_container_highest")
            round_rect(c, sx, sy, sx + sw, sy + sh, sh / 2, fill=track,
                       outline="" if on else t.c("outline"))
            tr = sh / 2 - 5
            tcx = (sx + sw - tr - 5) if on else (sx + tr + 5)
            tcy = sy + sh / 2
            c.create_oval(tcx - tr, tcy - tr, tcx + tr, tcy + tr,
                          fill=t.c("on_primary") if on else t.c("outline"),
                          outline="")
            self._setting_hot.append((sx - 10, sy - 10, sx + sw + 10, sy + sh + 10, key))
            y += row_h + 10

        # 关于
        y += 12
        c.create_text(6, y, text="关于", anchor="nw", fill=t.c("on_surface"),
                      font=self.fonts.get(TYPE_SCALE["title"], "bold"))
        c.create_text(6, y + 30,
                      text=f"pan-accelerator {__version__} · Material 3 Expressive",
                      anchor="nw", fill=t.c("on_surface_variant"),
                      font=self.fonts.get(TYPE_SCALE["body"]))
        c.create_text(6, y + 54,
                      text="仅下载你有权下载的内容，遵守各网盘服务条款。",
                      anchor="nw", fill=t.c("outline"), font=self.fonts.get(11))

    def _on_settings_click(self, e):
        for x1, y1, x2, y2, key in self._setting_hot:
            if x1 <= e.x <= x2 and y1 <= e.y <= y2:
                self._setting_state[key] = not self._setting_state.get(key, False)
                if key == "dark":
                    self.dark = self._setting_state["dark"]
                    self._rebuild_theme()
                elif key == "open_after":
                    self._open_after = self._setting_state[key]
                self._draw_settings()
                return

    # ---------------------------------------------------------------- 交互
    def _switch_page(self, index):
        for i, page in enumerate(self.pages):
            if i == index:
                page.pack(fill="both", expand=True)
            else:
                page.pack_forget()
        self._page = index
        if index == 1:
            self._draw_stats()
        elif index == 2:
            self._draw_appearance()
        elif index == 3:
            self._draw_settings()

    def _switch_mode(self, index):
        if index == 1:
            self.url_field.placeholder = "粘贴 magnet:?xt=urn:btih:… 或 .torrent 路径"
        else:
            self.url_field.placeholder = "粘贴下载直链，或整段浏览器「Copy as cURL」命令…"
        self.url_field._show_placeholder()
        self.md5_field.placeholder = ("可选" if index == 1 else "可选，粘贴后自动校验")
        self.md5_field._show_placeholder()

    def _on_threads(self, v):
        self.thread_value.configure(text=str(int(v)))

    def _pick_dir(self):
        d = filedialog.askdirectory(title="选择下载目录", initialdir=self._out_dir)
        if d:
            self._out_dir = d
            self.out_label.configure(text=self._short_path(d))

    def _short_path(self, p: str, limit=34) -> str:
        return p if len(p) <= limit else "…" + p[-(limit - 1):]

    def _start_task(self):
        src = self.url_field.get()
        if not src:
            self._toast("请先粘贴下载链接")
            return
        low = src.lower()
        if self.mode_seg.index == 1 or low.startswith("magnet:") or low.endswith(".torrent"):
            kind = TaskKind.BT
        elif low.startswith("thunder://"):
            kind = TaskKind.THUNDER
        else:
            kind = TaskKind.HTTP
        task = Task(
            tid=self._next_id, kind=kind, source=src,
            output=self._out_dir if kind == TaskKind.BT else "",
            threads=int(self.thread_slider.value), chunk_mb=32,
            expect_md5=self.md5_field.get(),
        )
        self._next_id += 1
        self.tasks.append(task)
        self._add_card(task)
        runner = TaskRunner(task, emit=self._on_task_update)
        self.runners[task.tid] = runner
        runner.start()
        self.url_field.clear()
        self.md5_field.clear()
        self.url_field._show_placeholder()
        self.md5_field._show_placeholder()
        self._update_count()

    def _add_card(self, task):
        if getattr(self, "empty_hint", None) and self.empty_hint.winfo_exists():
            self.empty_hint.pack_forget()
        card = TaskCard(self.task_frame, self.theme, task,
                        on_pause=self._toggle_pause,
                        on_remove=self._remove_task,
                        on_open=self._open_task,
                        fonts=self.fonts)
        card.pack(fill="x", pady=(0, 10))
        self.cards[task.tid] = card

    def _toggle_pause(self, task):
        runner = self.runners.get(task.tid)
        if task.active and runner:
            runner.stop()
            task.state = TaskState.PAUSED
        elif runner:
            runner.start()
        else:
            self._toast("任务已结束，无法续传")
        card = self.cards.get(task.tid)
        if card:
            card.render()

    def _remove_task(self, task):
        runner = self.runners.pop(task.tid, None)
        if runner:
            runner.stop()
        card = self.cards.pop(task.tid, None)
        if card:
            card.destroy()
        self.tasks = [x for x in self.tasks if x.tid != task.tid]
        if not self.tasks and getattr(self, "empty_hint", None):
            self.empty_hint.pack(pady=70)
        self._update_count()

    def _open_task(self, task):
        p = Path(task.output)
        target = p.parent if p.is_file() else p
        try:
            if sys.platform == "win32":
                os.startfile(target)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                os.system(f'open "{target}"')
            else:
                os.system(f'xdg-open "{target}"')
        except Exception as exc:  # noqa: BLE001
            self._toast(f"打开失败: {exc}")

    def _clear_done(self):
        for task in list(self.tasks):
            if task.state in (TaskState.DONE, TaskState.FAILED):
                self._remove_task(task)

    def _on_theme_switch(self, value):
        self.dark = bool(value)
        self._setting_state["dark"] = self.dark
        self._rebuild_theme()

    def _set_seed(self, seed):
        self.seed = seed
        self._rebuild_theme()
        self._draw_appearance()

    def _rebuild_theme(self):
        self.theme = make_theme(dark=self.dark, seed=self.seed)
        self._apply_theme()

    def _apply_theme(self):
        t = self.theme
        self.configure(bg=t.c("surface"))
        for wdg in (self.header, self.body, self.content, *self.pages):
            try:
                wdg.configure(bg=t.c("surface"))
            except Exception:
                pass
        self._theme_recursive(self)
        self._draw_header()
        self._layout_composer()
        for card in list(self.cards.values()):
            card.set_theme(t)
        if self._page == 0:
            self._theme_download_page()
        elif self._page == 1:
            self._draw_stats()
        elif self._page == 2:
            self._draw_appearance()
        elif self._page == 3:
            self._draw_settings()

    def _theme_download_page(self):
        t = self.theme
        for w in (self.count_label, self.empty_hint):
            try:
                w.configure(bg=t.c("surface"))
                if isinstance(w, tk.Label):
                    w.configure(fg=t.c("on_surface_variant"))
            except Exception:
                pass
        try:
            self.canvas.configure(bg=t.c("surface"))
            self.task_frame.configure(bg=t.c("surface"))
        except Exception:
            pass
        try:
            self.composer.configure(bg=t.c("surface_container"))
            self.composer_opts.configure(bg=t.c("surface_container"))
            for w in self.composer_opts.winfo_children():
                if isinstance(w, tk.Label):
                    w.configure(bg=t.c("surface_container"))
        except Exception:
            pass
        if getattr(self, "empty_hint", None):
            self.empty_hint.configure(bg=t.c("surface"), fg=t.c("on_surface_variant"))

    def _theme_recursive(self, widget):
        t = self.theme
        for child in widget.winfo_children():
            if hasattr(child, "set_theme"):
                try:
                    child.set_theme(t)
                except Exception:
                    pass
                self._theme_recursive(child)
                continue
            if isinstance(child, (tk.Frame, tk.Canvas, tk.Label)):
                try:
                    child.configure(bg=t.c("surface"))
                except Exception:
                    pass
                if isinstance(child, tk.Label):
                    try:
                        child.configure(fg=t.c("on_surface_variant"))
                    except Exception:
                        pass
            self._theme_recursive(child)

    # ------------------------------------------------------------- 进度回报
    def _on_task_update(self, task):
        self._ui_queue.put(("update", task))

    def _pump(self):
        latest: dict[int, Task] = {}
        try:
            while True:
                kind, payload = self._ui_queue.get_nowait()
                if kind == "update":
                    latest[payload.tid] = payload
        except queue.Empty:
            pass
        for task in latest.values():
            card = self.cards.get(task.tid)
            if card:
                card.render()
        if latest:
            self._update_count()
            if self._page == 1:
                self._draw_stats()
            for task in latest.values():
                if task.state == TaskState.DONE:
                    self.runners.pop(task.tid, None)
                    if self._open_after and task.output:
                        self._open_task(task)
        self.after(70, self._pump)

    def _update_count(self):
        n = len(self.tasks)
        active = sum(1 for x in self.tasks if x.active)
        text = f"队列 · {n} 个任务"
        if active:
            text += f"（{active} 进行中）"
        self.count_label.configure(text=text)

    # ---------------------------------------------------------------- 滚动
    def _on_task_frame_configure(self, _):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, e):
        self.canvas.itemconfigure(self._task_window, width=e.width)

    def _on_wheel(self, e):
        if self._page == 0:
            self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

    # ---------------------------------------------------------------- 提示
    def _toast(self, msg):
        t = self.theme
        top = tk.Toplevel(self)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        f = self.fonts.get(12)
        width = f.measure(msg) + 48
        cv = tk.Canvas(top, width=width, height=48, bg=t.c("inverse_surface"),
                       highlightthickness=0)
        cv.pack()
        cv.create_text(width / 2, 24, text=msg,
                       fill=t.c("inverse_on_surface"), font=f)
        self.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - width) // 2
        y = self.winfo_rooty() + self.winfo_height() - 140
        top.geometry(f"{width}x48+{x}+{y}")
        top.after(2000, top.destroy)

    def _human(self, n: float) -> str:
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or unit == "TB":
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"


def main():
    dark = "--light" not in sys.argv
    seed = SEEDS["青蓝"]
    for i, a in enumerate(sys.argv):
        if a == "--seed" and i + 1 < len(sys.argv):
            seed = sys.argv[i + 1]
    app = MainWindow(seed=seed, dark=dark)
    app.mainloop()


if __name__ == "__main__":
    main()
