"""自绘 Material 3 Expressive 组件库（tkinter Canvas 实现）。

为什么自绘：tkinter 原生控件无法表达 M3 的大圆角、tonal 表面、state layer、
ripple 与 spring 动效。这里用 Canvas 直接绘制，换取完整的视觉控制力。

所有组件遵循同一套接口：
    Widget(master, theme=..., **opts)
    .set_theme(theme)  → 主题热切换
"""
from __future__ import annotations

import math
import tkinter as tk
import tkinter.font as tkfont

from .tokens import Theme, hex_to_rgb, mix, rgb_to_hex, contrast_ratio, with_alpha


# ---------------------------------------------------------------------------
# 绘图基础
# ---------------------------------------------------------------------------


def round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kw):
    """在 Canvas 上画圆角矩形（用平滑多边形逼近，避免拼接弧线的接缝）。"""
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    points = []
    steps = 6
    # 四个角的圆弧采样点
    corners = [
        (x2 - r, y1 + r, 270, 360),  # 右上
        (x2 - r, y2 - r, 0, 90),     # 右下
        (x1 + r, y2 - r, 90, 180),   # 左下
        (x1 + r, y1 + r, 180, 270),  # 左上
    ]
    for cx, cy, a0, a1 in corners:
        for i in range(steps + 1):
            a = math.radians(a0 + (a1 - a0) * i / steps)
            points.extend((cx + r * math.cos(a), cy + r * math.sin(a)))
    return canvas.create_polygon(points, smooth=True, **kw)


def shadow_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, color, *, spread=8, alpha=0.12, steps=5):
    """柔和阴影：多层半透明圆角矩形向外扩散（M3 elevation）。"""
    for i in range(steps, 0, -1):
        t = i / steps
        off = spread * t
        shade = rgb_to_hex(mix(hex_to_rgb(color), hex_to_rgb(color), 0))
        round_rect(canvas, x1 - off, y1 - off + 1, x2 + off, y2 + off + 1, r + off,
                   fill=shade, outline="", stipple=_stipple(alpha * (1 - t) * 0.5))


def _stipple(alpha: float) -> str:
    """用 tkinter stipple 近似透明度（灰度图案，越密越不透明）。"""
    if alpha <= 0.05:
        return "gray12"
    if alpha <= 0.12:
        return "gray12"
    if alpha <= 0.25:
        return "gray25"
    if alpha <= 0.45:
        return "gray50"
    return "gray75"


def ease_spring(t: float) -> float:
    """Expressive spring：有轻微过冲的落定。"""
    if t <= 0:
        return 0.0
    if t >= 1:
        return 1.0
    return 1 - math.exp(-6.5 * t) * math.cos(9.0 * t)


def ease_out(t: float) -> float:
    return 1 - (1 - t) ** 3


# ---------------------------------------------------------------------------
# 字体
# ---------------------------------------------------------------------------


class Fonts:
    """按 DPI 缩放的一组字体。"""

    def __init__(self, root: tk.Misc, scale: float = 1.0):
        self.scale = scale

        def fam(*names):
            available = set(tkfont.families(root))
            for n in names:
                if n in available:
                    return n
            return "TkDefaultFont"

        self.family = fam("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC",
                          "Noto Sans CJK SC", "Segoe UI")
        self.mono = fam("Cascadia Mono", "JetBrains Mono", "Consolas", "Menlo")
        self._root = root
        self._cache: dict = {}

    def get(self, size: int, weight: str = "normal") -> tkfont.Font:
        key = (size, weight)
        if key not in self._cache:
            self._cache[key] = tkfont.Font(
                root=self._root, family=self.family,
                size=-int(round(size * self.scale)), weight=weight)
        return self._cache[key]

    def mono_get(self, size: int) -> tkfont.Font:
        key = ("mono", size)
        if key not in self._cache:
            self._cache[key] = tkfont.Font(
                root=self._root, family=self.mono,
                size=-int(round(size * self.scale)))
        return self._cache[key]


# M3 Expressive 字阶（role → px）
TYPE_SCALE = {
    "display": 32, "headline": 24, "title_lg": 20, "title": 16,
    "body_lg": 14, "body": 13, "label": 12, "label_sm": 11, "caption": 10,
}


# ---------------------------------------------------------------------------
# 基础组件
# ---------------------------------------------------------------------------


class Surface(tk.Canvas):
    """tonal 表面卡片：圆角 + 可选柔和阴影 + 可选描边。"""

    def __init__(self, master, theme: Theme, *, radius=None, role="surface_container",
                 elevation=0, outline=False, padding=16, **kw):
        bg = theme.c("surface")
        super().__init__(master, bg=bg, highlightthickness=0, bd=0,
                         takefocus=0, **kw)
        self.theme = theme
        self.radius = theme.radius_lg if radius is None else radius
        self.role = role
        self.elevation = elevation
        self.has_outline = outline
        self.padding = padding
        self._items: list[int] = []
        self.bind("<Configure>", lambda e: self._redraw())

    def set_theme(self, theme: Theme):
        self.theme = theme
        self.configure(bg=theme.c("surface"))
        self._redraw()

    def set_role(self, role: str, redraw=True):
        self.role = role
        if redraw:
            self._redraw()

    def _redraw(self):
        for i in self._items:
            self.delete(i)
        self._items.clear()
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 2 or h <= 2:
            return
        t = self.theme
        if self.elevation:
            for k in range(self.elevation, 0, -1):
                pad = k * 2
                self._items.append(round_rect(
                    self, pad, pad + 2, w - pad, h - pad + 2, self.radius,
                    fill=t.c("shadow"), outline="", stipple="gray12"))
        self._items.append(round_rect(
            self, 0, 0, w - 1, h - 1, self.radius,
            fill=t.c(self.role),
            outline=t.c("outline_variant") if self.has_outline else "",
            width=1))
        self.tag_lower(self._items[-1])

    def body(self) -> tuple[int, int]:
        """返回内容区域的左上角内边距。"""
        return self.padding, self.padding


class RoundedButton(tk.Canvas):
    """M3 Expressive 按钮：filled / tonal / outlined / text 四种形态。

    包含 hover 状态层、按下缩放与 ripple 涟漪。
    """

    VARIANTS = {
        # variant: (容器色, 前景色, 是否有描边)
        "filled": ("primary", "on_primary", False),
        "tonal": ("secondary_container", "on_secondary_container", False),
        "outlined": (None, "primary", True),
        "text": (None, "primary", False),
        "danger": ("error", "on_error", False),
    }

    def __init__(self, master, theme: Theme, text="", command=None, *,
                 variant="filled", radius=None, height=44, width=None,
                 icon=None, font_size=None, **kw):
        bg = theme.c("surface")
        super().__init__(master, bg=bg, highlightthickness=0, bd=0,
                         cursor="hand2", takefocus=1, **kw)
        self.theme = theme
        self.text = text
        self.icon = icon
        self.command = command
        self.variant = variant
        self.radius = theme.radius_full if radius is None else radius
        self.h = height
        self.font_size = font_size or TYPE_SCALE["label"] + 1
        self._hover = False
        self._pressed = False
        self._enabled = True
        self._ripple = None
        self._scale_anim = 0.0
        self._items: list[int] = []

        self._measure(width)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Return>", lambda e: self._fire())
        self.bind("<space>", lambda e: self._fire())

    # -- public
    def set_theme(self, theme: Theme):
        self.theme = theme
        self.configure(bg=theme.c("surface"))
        self._redraw()

    def set_enabled(self, on: bool):
        self._enabled = bool(on)
        self.configure(cursor="hand2" if on else "arrow")
        self._redraw()

    def set_text(self, text: str):
        self.text = text
        self._measure(None)
        self._redraw()

    def set_variant(self, variant: str):
        self.variant = variant
        self._redraw()

    # -- 内部
    def _measure(self, width):
        f = self._font()
        tw = f.measure(self.text) if self.text else 0
        iw = 22 if self.icon else 0
        pad = 26 if self.variant != "text" else 14
        need = tw + iw + pad * 2
        self.configure(width=width or max(need, 64), height=self.h)

    def _font(self):
        return self.master.winfo_toplevel().fonts.get(self.font_size, "bold") \
            if hasattr(self.master.winfo_toplevel(), "fonts") else tkfont.Font(size=-12)

    def _colors(self):
        t = self.theme
        container, label, _ = self.VARIANTS[self.variant]
        base = t.c("surface")
        if container is None:
            bgc = base
        else:
            bgc = t.c(container)
            if not self._enabled:
                bgc = t.alpha(container, 0.38, "surface")
        fgc = t.c(label)
        if not self._enabled:
            fgc = t.alpha(label, 0.38, "surface")
        return bgc, fgc

    def _redraw(self):
        for i in self._items:
            self.delete(i)
        self._items.clear()
        if self._ripple:
            self.delete(self._ripple)
            self._ripple = None
        t = self.theme
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 2 or h <= 2:
            return
        bgc, fgc = self._colors()
        _, _, outlined = self.VARIANTS[self.variant]

        # 按下时轻微内缩（Expressive 的按压反馈）
        inset = 1 if self._pressed else 0
        x1, y1, x2, y2 = inset, inset, w - 1 - inset, h - 1 - inset
        r = self.radius if self.radius < 900 else (y2 - y1) / 2

        if not self._enabled:
            # 禁用态不画状态层
            self._items.append(round_rect(
                self, x1, y1, x2, y2, r, fill=bgc,
                outline=t.alpha("on_surface", 0.12, "surface") if outlined else "",
                width=1))
        else:
            container = self.VARIANTS[self.variant][0]
            fill = bgc
            if self._hover and container:
                # M3 state layer：hover 8%
                fill = rgb_to_hex(mix(hex_to_rgb(bgc), hex_to_rgb(fgc), 0.08))
            elif self._hover:
                fill = t.alpha("primary", 0.08, "surface")
            self._items.append(round_rect(
                self, x1, y1, x2, y2, r, fill=fill,
                outline=t.c("outline") if outlined else "", width=1))

        cx = w / 2
        label = self.text
        f = self._font()
        if self.icon and label:
            total = f.measure(label) + 22
            ix = cx - total / 2
            self._items.append(self.create_text(
                ix + 8, h / 2, text=self.icon, fill=fgc,
                font=(self.theme and "" or "", int(self.font_size + 2)), anchor="c"))
            self._items.append(self.create_text(
                ix + 22 + f.measure(label) / 2, h / 2, text=label, fill=fgc,
                font=f, anchor="c"))
        elif label:
            self._items.append(self.create_text(
                cx, h / 2, text=label, fill=fgc, font=f, anchor="c"))
        elif self.icon:
            self._items.append(self.create_text(
                cx, h / 2, text=self.icon, fill=fgc,
                font=("", int(self.font_size + 2)), anchor="c"))

    # -- 交互
    def _on_enter(self, _):
        if not self._enabled:
            return
        self._hover = True
        self._animate_hover()

    def _on_leave(self, _):
        self._hover = False
        self._pressed = False
        self._redraw()

    def _on_press(self, e):
        if not self._enabled:
            return
        self._pressed = True
        self._redraw()
        self._spawn_ripple(e.x, e.y)

    def _on_release(self, e):
        if not self._enabled:
            return
        was = self._pressed
        self._pressed = False
        self._redraw()
        if was and 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            self._fire()

    def _fire(self):
        if self._enabled and self.command:
            self.command()

    def _animate_hover(self):
        """hover 状态层的淡入（用重绘近似，够轻量）。"""
        self._redraw()

    def _spawn_ripple(self, x, y):
        """涟漪：一个从小变大的半透明圆。"""
        if self._ripple:
            self.delete(self._ripple)
        t = self.theme
        _, fgc = self._colors()
        self._ripple = self.create_oval(x - 2, y - 2, x + 2, y + 2,
                                        fill=t.alpha("on_surface", 0.10, "surface"),
                                        outline="")
        self._ripple_center = (x, y)
        self._ripple_step = 0
        self._ripple_max = 26
        self.after(16, self._ripple_tick)

    def _ripple_tick(self):
        if not self._ripple:
            return
        self._ripple_step += 1
        k = self._ripple_step / 10
        if k >= 1:
            if self._ripple:
                self.delete(self._ripple)
                self._ripple = None
            return
        r = 4 + self._ripple_max * ease_out(k)
        x, y = self._ripple_center
        self.coords(self._ripple, x - r, y - r, x + r, y + r)
        self.tag_raise(self._ripple)
        self.after(16, self._ripple_tick)


class IconButton(RoundedButton):
    """圆形图标按钮。"""

    def __init__(self, master, theme, icon="", command=None, *, size=44, **kw):
        super().__init__(master, theme, text="", command=command, icon=icon,
                         width=size, height=size, radius=theme.radius_full, **kw)


class Switch(tk.Canvas):
    """M3 开关：带滑块 spring 位移与轨道颜色过渡。"""

    def __init__(self, master, theme: Theme, *, value=False, command=None,
                 width=56, height=32, **kw):
        super().__init__(master, width=width, height=height, bg=theme.c("surface"),
                         highlightthickness=0, bd=0, cursor="hand2", **kw)
        self.theme = theme
        self.value = bool(value)
        self.command = command
        self.w, self.h = width, height
        self._pos = 1.0 if value else 0.0
        self._anim = None
        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Button-1>", self._toggle)

    def set_theme(self, theme: Theme):
        self.theme = theme
        self.configure(bg=theme.c("surface"))
        self._redraw()

    def set_value(self, v, notify=False):
        v = bool(v)
        if v == self.value:
            return
        self.value = v
        self._animate(1.0 if v else 0.0)
        if notify and self.command:
            self.command(self.value)

    def _toggle(self, _=None):
        self.set_value(not self.value, notify=True)

    def _animate(self, target):
        if self._anim:
            self.after_cancel(self._anim)
            self._anim = None
        start = self._pos

        def step(i):
            k = min(1.0, i / 8)
            self._pos = start + (target - start) * ease_spring(k)
            self._redraw()
            if k < 1:
                self._anim = self.after(16, lambda: step(i + 1))
            else:
                self._pos = target
                self._anim = None
                self._redraw()

        step(0)

    def _redraw(self):
        self.delete("all")
        t = self.theme
        w, h = self.winfo_width() or self.w, self.winfo_height() or self.h
        r = h / 2
        track_off = t.c("surface_container_highest")
        track_on = t.c("primary")
        track = rgb_to_hex(mix(hex_to_rgb(track_off), hex_to_rgb(track_on), self._pos))
        round_rect(self, 1, 1, w - 1, h - 1, r - 1,
                   fill=track, outline=t.c("outline") if self._pos < 0.5 else "", width=1)

        thumb_r = r - 5
        x_off = thumb_r + 6
        x_on = w - thumb_r - 6
        cx = x_off + (x_on - x_off) * self._pos
        cy = h / 2
        thumb = t.c("on_primary") if self._pos > 0.5 else t.c("outline")
        self.create_oval(cx - thumb_r, cy - thumb_r, cx + thumb_r, cy + thumb_r,
                         fill=thumb, outline="")


class Slider(tk.Canvas):
    """M3 滑块：轨道 + 活性指示点 + 拖拽手柄，数值吸附与弹簧回弹。"""

    def __init__(self, master, theme: Theme, *, minimum=1, maximum=32, value=8,
                 step=1, command=None, width=260, height=40, **kw):
        super().__init__(master, width=width, height=height, bg=theme.c("surface"),
                         highlightthickness=0, bd=0, cursor="hand2", **kw)
        self.theme = theme
        self.min, self.max = minimum, maximum
        self.step = step
        self.value = value
        self.command = command
        self._dragging = False
        self._hover = False
        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", lambda e: (setattr(self, "_hover", True), self._redraw()))
        self.bind("<Leave>", lambda e: (setattr(self, "_hover", False), self._redraw()))

    def set_theme(self, theme):
        self.theme = theme
        self.configure(bg=theme.c("surface"))
        self._redraw()

    def set_value(self, v, notify=False):
        v = max(self.min, min(self.max, v))
        v = round(v / self.step) * self.step
        if v == self.value:
            return
        self.value = v
        self._redraw()
        if notify and self.command:
            self.command(self.value)

    def _ratio(self):
        span = self.max - self.min
        return 0 if span <= 0 else (self.value - self.min) / span

    def _x_to_value(self, x):
        pad = 18
        w = self.winfo_width() or 260
        usable = max(1, w - pad * 2)
        t = max(0.0, min(1.0, (x - pad) / usable))
        return self.min + t * (self.max - self.min)

    def _on_press(self, e):
        self._dragging = True
        self.set_value(self._x_to_value(e.x), notify=True)

    def _on_drag(self, e):
        if self._dragging:
            self.set_value(self._x_to_value(e.x), notify=True)

    def _on_release(self, _):
        self._dragging = False
        self._redraw()

    def _redraw(self):
        self.delete("all")
        t = self.theme
        w = self.winfo_width() or 260
        h = self.winfo_height() or 40
        pad = 18
        cy = h / 2
        track_h = 6 if self._hover or self._dragging else 4
        ratio = self._ratio()

        round_rect(self, pad, cy - track_h / 2, w - pad, cy + track_h / 2,
                   track_h / 2, fill=t.c("surface_container_highest"), outline="")
        active_end = pad + (w - pad * 2) * ratio
        if ratio > 0:
            round_rect(self, pad, cy - track_h / 2, active_end, cy + track_h / 2,
                       track_h / 2, fill=t.c("primary"), outline="")

        # 活性指示点（M3 Expressive 的 attention dot）
        if 0 < ratio < 1:
            self.create_oval(active_end - 2, cy - 2, active_end + 2, cy + 2,
                             fill=t.c("on_primary"), outline="")

        thumb_r = 9 if (self._hover or self._dragging) else 7
        self.create_oval(active_end - thumb_r, cy - thumb_r,
                         active_end + thumb_r, cy + thumb_r,
                         fill=t.c("primary"), outline=t.c("surface"), width=3)


class ProgressRing(tk.Canvas):
    """环形进度：BT/整体任务用，带动画补间。"""

    def __init__(self, master, theme: Theme, *, size=64, thickness=6,
                 value=0.0, **kw):
        super().__init__(master, width=size, height=size, bg=theme.c("surface"),
                         highlightthickness=0, bd=0, **kw)
        self.theme = theme
        self.size = size
        self.thickness = thickness
        self._value = value
        self._shown = value
        self.bind("<Configure>", lambda e: self._redraw())

    def set_theme(self, theme):
        self.theme = theme
        self.configure(bg=theme.c("surface"))
        self._redraw()

    def set_value(self, v: float, animate=True):
        self._value = max(0.0, min(1.0, v))
        if not animate:
            self._shown = self._value
            self._redraw()
            return
        self._animate_to(self._value)

    def _animate_to(self, target):
        start = self._shown

        def step(i):
            k = min(1.0, i / 10)
            self._shown = start + (target - start) * ease_out(k)
            self._redraw()
            if k < 1:
                self.after(16, lambda: step(i + 1))

        step(0)

    def _redraw(self):
        self.delete("all")
        t = self.theme
        s = min(self.winfo_width() or self.size, self.winfo_height() or self.size)
        pad = self.thickness / 2 + 2
        box = (pad, pad, s - pad, s - pad)
        self.create_oval(*box, outline=t.c("surface_container_highest"),
                         width=self.thickness)
        extent = -359.99 * self._shown
        if self._shown > 0.001:
            self.create_arc(*box, start=90, extent=extent, style="arc",
                            outline=t.c("primary"), width=self.thickness)


class Sparkline(tk.Canvas):
    """速度波形图：滚动窗口 + 渐变填充（用 stipple 模拟）。"""

    def __init__(self, master, theme: Theme, *, width=320, height=64,
                 max_points=90, color_role="primary", **kw):
        super().__init__(master, width=width, height=height, bg=theme.c("surface"),
                         highlightthickness=0, bd=0, **kw)
        self.theme = theme
        self.max_points = max_points
        self.color_role = color_role
        self.points: list[float] = []
        self.bind("<Configure>", lambda e: self._redraw())

    def set_theme(self, theme):
        self.theme = theme
        self.configure(bg=theme.c("surface"))
        self._redraw()

    def push(self, value: float):
        self.points.append(max(0.0, float(value)))
        if len(self.points) > self.max_points:
            self.points = self.points[-self.max_points:]
        self._redraw()

    def clear(self):
        self.points.clear()
        self._redraw()

    def _redraw(self):
        self.delete("all")
        if len(self.points) < 2:
            return
        t = self.theme
        w = self.winfo_width() or 320
        h = self.winfo_height() or 64
        top = h * 0.18
        bottom = h - 6
        peak = max(self.points) or 1.0
        n = len(self.points)
        step = w / max(1, (self.max_points - 1))
        offset = w - step * (n - 1)

        coords = []
        for i, v in enumerate(self.points):
            x = offset + i * step
            y = bottom - (v / peak) * (bottom - top)
            coords.extend((x, y))
        if len(coords) < 4:
            return

        # 面积填充（半透明近似）
        poly = [offset, bottom] + coords + [offset + (n - 1) * step, bottom]
        self.create_polygon(poly, fill=t.alpha(self.color_role, 0.16, "surface"),
                            outline="")
        # 折线
        self.create_line(coords, fill=t.c(self.color_role), width=2,
                         smooth=True, splinesteps=12)


class SegmentedControl(tk.Canvas):
    """M3 分段按钮：选中项有 tonal 背景与滑块指示。"""

    def __init__(self, master, theme: Theme, options: list[str], *,
                 index=0, command=None, height=40, **kw):
        super().__init__(master, bg=theme.c("surface_container"), height=height,
                         highlightthickness=0, bd=0, **kw)
        self.theme = theme
        self.options = options
        self.index = index
        self.command = command
        self.h = height
        self._items = []
        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Button-1>", self._on_click)

    def set_theme(self, theme):
        self.theme = theme
        self.configure(bg=theme.c("surface_container"))
        self._redraw()

    def set_options(self, options, index=None):
        self.options = options
        self.index = min(self.index if index is None else index, len(options) - 1)
        self._redraw()

    def select(self, index, notify=True):
        if index == self.index or not (0 <= index < len(self.options)):
            return
        self.index = index
        self._redraw()
        if notify and self.command:
            self.command(self.index)

    def _font(self):
        top = self.master.winfo_toplevel()
        return top.fonts.get(TYPE_SCALE["label"] + 1, "bold") if hasattr(top, "fonts") \
            else tkfont.Font(size=-12)

    def _on_click(self, e):
        w = self.winfo_width()
        n = len(self.options)
        if n:
            self.select(min(n - 1, int(e.x / (w / n))))

    def _redraw(self):
        for i in self._items:
            try:
                self.delete(i)
            except Exception:
                pass
        self._items.clear()
        t = self.theme
        w = self.winfo_width()
        h = self.winfo_height() or self.h
        n = len(self.options)
        if w <= 4 or n == 0:
            return
        round_rect(self, 0, 0, w - 1, h - 1, h / 2, fill=t.c("surface_container"), outline="")
        seg = w / n
        pad = 3
        f = self._font()
        for i, opt in enumerate(self.options):
            x1 = i * seg + pad
            x2 = (i + 1) * seg - pad
            if i == self.index:
                self._items.append(round_rect(
                    self, x1, pad, x2, h - pad, (h - pad * 2) / 2,
                    fill=t.c("secondary_container"), outline=""))
                color = t.c("on_secondary_container")
            else:
                color = t.c("on_surface_variant")
            self._items.append(self.create_text(
                (x1 + x2) / 2, h / 2, text=opt, fill=color, font=f, anchor="c"))


class TextField(tk.Canvas):
    """M3 输入框：圆角容器 + 聚焦时的主色描边 + 占位符。"""

    def __init__(self, master, theme: Theme, *, placeholder="", height=52,
                 role="surface_container_high", multiline=False, **kw):
        super().__init__(master, bg=theme.c("surface"), height=height,
                         highlightthickness=0, bd=0, **kw)
        self.theme = theme
        self.placeholder = placeholder
        self.role = role
        self.multiline = multiline
        self._focus = False
        self._items = []

        tk.Misc  # 占位，说明这是自定义控件
        self.entry = tk.Text(self, bd=0, highlightthickness=0, wrap="word" if multiline else "none",
                             height=1 if not multiline else 2, undo=True,
                             insertwidth=2, padx=12, pady=10, relief="flat")
        self.entry.configure(bg=theme.c(role), fg=theme.c("on_surface"),
                             insertbackground=theme.c("primary"),
                             selectbackground=theme.c("primary_container"),
                             selectforeground=theme.c("on_primary_container"))
        top = master.winfo_toplevel()
        if hasattr(top, "fonts"):
            self.entry.configure(font=top.fonts.get(TYPE_SCALE["body"] + 1))
            self.top_fonts = top.fonts
        self._win = self.create_window(0, 0, window=self.entry, anchor="nw")
        self.entry.bind("<FocusIn>", self._on_focus_in)
        self.entry.bind("<FocusOut>", self._on_focus_out)
        self.entry.bind("<KeyRelease>", lambda e: self._show_placeholder())
        self.entry.bind("<<Paste>>", lambda e: self.after(10, self._show_placeholder))
        self.bind("<Configure>", lambda e: self._layout())
        if not multiline:
            self.entry.bind("<Return>", lambda e: (self.event_generate("<<Submit>>"), "break")[1])
        self._placeholder_id = None
        self._ph_shown = False
        # 首帧可能还没有尺寸，稍后再画一次占位符
        self.after(60, self._show_placeholder)

    def set_theme(self, theme):
        self.theme = theme
        self.configure(bg=theme.c("surface"))
        self.entry.configure(bg=theme.c(self.role), fg=theme.c("on_surface"),
                             insertbackground=theme.c("primary"),
                             selectbackground=theme.c("primary_container"),
                             selectforeground=theme.c("on_primary_container"))
        self._layout()

    def get(self) -> str:
        if getattr(self, "_ph_shown", False):
            return ""
        return self.entry.get("1.0", "end-1c").strip()

    def set(self, text: str):
        self._clear_placeholder()
        self.entry.delete("1.0", "end")
        if text:
            self.entry.insert("1.0", text)

    def clear(self):
        self.set("")
        if not self._focus:
            self._show_placeholder()

    def _on_focus_in(self, _):
        self._focus = True
        self._clear_placeholder()
        self._layout()

    def _on_focus_out(self, _):
        self._focus = False
        self._show_placeholder()
        self._layout()

    def _layout(self):
        for i in self._items:
            try:
                self.delete(i)
            except Exception:
                pass
        self._items.clear()
        t = self.theme
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 4 or h <= 4:
            return
        r = t.radius_md
        self._items.append(round_rect(
            self, 0, 0, w - 1, h - 1, r, fill=t.c(self.role),
            outline=t.c("primary") if self._focus else t.c("outline_variant"),
            width=2 if self._focus else 1))
        for i in self._items:
            self.tag_lower(i)
        self.coords(self._win, 2, 2)
        self.itemconfigure(self._win, width=max(10, w - 4), height=max(10, h - 4))
        self._show_placeholder()

    def _show_placeholder(self):
        """占位符用 Text 自身的 tag 实现。

        Canvas 内嵌窗口总是盖在 canvas 绘图之上，所以不能靠 create_text 画占位符。
        """
        if not self.placeholder:
            return
        t = self.theme
        self.entry.tag_configure("placeholder", foreground=t.c("on_surface_variant"))
        has_text = self.entry.get("1.0", "end-1c") != ""
        showing = getattr(self, "_ph_shown", False)
        if not has_text and not self._focus and not showing:
            self.entry.insert("1.0", self.placeholder)
            self.entry.tag_add("placeholder", "1.0", "end")
            self._ph_shown = True
        elif self._focus and showing:
            self._clear_placeholder()

    def _clear_placeholder(self):
        if getattr(self, "_ph_shown", False):
            self.entry.delete("1.0", "end")
            self.entry.tag_remove("placeholder", "1.0", "end")
            self._ph_shown = False


class NavRail(tk.Canvas):
    """M3 导航栏：图标 + 标签，选中项带 tonal 指示条。"""

    def __init__(self, master, theme: Theme, items: list[tuple[str, str]], *,
                 index=0, command=None, width=88, **kw):
        super().__init__(master, width=width, bg=theme.c("surface"),
                         highlightthickness=0, bd=0, **kw)
        self.theme = theme
        self.items = items
        self.index = index
        self.command = command
        self.w = width
        self._hover = -1
        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Button-1>", self._on_click)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda e: (setattr(self, "_hover", -1), self._redraw()))

    def set_theme(self, theme):
        self.theme = theme
        self.configure(bg=theme.c("surface"))
        self._redraw()

    def select(self, index, notify=True):
        if index == self.index:
            return
        self.index = index
        self._redraw()
        if notify and self.command:
            self.command(index)

    def _slot_height(self):
        h = self.winfo_height()
        return h / max(1, len(self.items))

    def _on_click(self, e):
        slot = self._slot_height()
        i = int(e.y / slot)
        if 0 <= i < len(self.items):
            self.select(i)

    def _on_motion(self, e):
        slot = self._slot_height()
        i = int(e.y / slot)
        i = i if 0 <= i < len(self.items) else -1
        if i != self._hover:
            self._hover = i
            self._redraw()

    def _redraw(self):
        self.delete("all")
        t = self.theme
        w = self.winfo_width() or self.w
        slot = self._slot_height()
        top = self.master.winfo_toplevel()
        fonts = getattr(top, "fonts", None)
        for i, (icon, label) in enumerate(self.items):
            cy = slot * i + slot / 2
            selected = i == self.index
            if selected:
                round_rect(self, 12, cy - 26, w - 12, cy + 26, t.radius_md,
                           fill=t.c("secondary_container"), outline="")
                fg = t.c("on_secondary_container")
            elif i == self._hover:
                round_rect(self, 12, cy - 26, w - 12, cy + 26, t.radius_md,
                           fill=t.alpha("on_surface", 0.08, "surface"), outline="")
                fg = t.c("on_surface")
            else:
                fg = t.c("on_surface_variant")
            self.create_text(w / 2, cy - 8, text=icon, fill=fg,
                             font=("", 17) if fonts is None else fonts.get(17))
            self.create_text(w / 2, cy + 14, text=label, fill=fg,
                             font=fonts.get(TYPE_SCALE["label_sm"] + 1) if fonts
                             else ("", -11))


class Chip(tk.Canvas):
    """M3 小标签：用于状态、"已校验"等。"""

    def __init__(self, master, theme: Theme, text="", *, role="secondary_container",
                 fg="on_secondary_container", height=26, **kw):
        super().__init__(master, bg=theme.c("surface"), height=height,
                         highlightthickness=0, bd=0, **kw)
        self.theme = theme
        self.text = text
        self.role = role
        self.fg = fg
        self.h = height
        self.bind("<Configure>", lambda e: self._redraw())

    def set(self, text=None, role=None, fg=None):
        if text is not None:
            self.text = text
        if role is not None:
            self.role = role
        if fg is not None:
            self.fg = fg
        self._redraw()

    def set_theme(self, theme):
        self.theme = theme
        self.configure(bg=theme.c("surface"))
        self._redraw()

    def _redraw(self):
        self.delete("all")
        t = self.theme
        w = self.winfo_width()
        h = self.winfo_height() or self.h
        top = self.master.winfo_toplevel()
        fonts = getattr(top, "fonts", None)
        f = fonts.get(TYPE_SCALE["label_sm"] + 1, "bold") if fonts else tkfont.Font(size=-11)
        tw = f.measure(self.text) + 20 if self.text else 0
        if w < tw:
            self.configure(width=tw)
            w = tw
        round_rect(self, 0, 0, w - 1, h - 1, h / 2, fill=t.c(self.role), outline="")
        self.create_text(w / 2, h / 2, text=self.text, fill=t.c(self.fg), font=f)
