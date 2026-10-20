"""Material 3 Expressive 设计令牌与主题引擎。

参考 Material Design 3 (Material You) 的 tonal palette 算法：
由一颗种子色生成 0-100 的色调阶梯，再映射成语义角色（primary / surface / outline ...）。
同时提供 Android 17 强调的 Expressive 语汇：大圆角、shape morphing、spring 曲线、tonal 表面。
"""
from __future__ import annotations

import colorsys
import math
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 色彩数学
# ---------------------------------------------------------------------------


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def rgb_to_hex(rgb) -> str:
    r, g, b = (max(0, min(255, int(round(c)))) for c in rgb[:3])
    return f"#{r:02X}{g:02X}{b:02X}"


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c: float) -> float:
    return c * 12.92 if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def luminance(rgb) -> float:
    """WCAG 相对亮度，用于挑选可读的前景色。"""
    r, g, b = (_srgb_to_linear(c / 255) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def mix(a, b, t: float):
    """线性插值两个颜色，t=0 取 a，t=1 取 b。"""
    t = max(0.0, min(1.0, t))
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def over(fg, bg, alpha: float):
    """把带透明度的前景色合成到背景上。"""
    return mix(bg, fg, alpha)


def with_alpha(color, alpha: float, bg):
    return over(color, bg, alpha)


def contrast_ratio(a, b) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def on_color(bg, light=(255, 255, 255), dark=(16, 18, 22)) -> tuple[int, int, int]:
    """在给定背景上挑一个可读的对比色。"""
    return dark if contrast_ratio(bg, dark) >= contrast_ratio(bg, light) else light


# ---------------------------------------------------------------------------
# Tonal palette（M3 核心）
# ---------------------------------------------------------------------------


def _hct_tone_to_rgb(hue: float, chroma: float, tone: float) -> tuple[int, int, int]:
    """简化版 HCT → sRGB。

    M3 的 HCT 以「感知色调 tone(0-100)」代替 HSL 的 lightness，
    这里用 HSL 加经验修正逼近，够用于 UI 取色。
    """
    # tone → HSL lightness 的非线性映射（中段更平缓，暗端下沉）
    t = max(0.0, min(100.0, tone)) / 100.0
    light = t ** 1.15
    sat = max(0.0, min(1.0, chroma / 100.0))
    # 暗端和亮端降低饱和度，避免过饱和荧光感
    sat *= 1.0 - abs(t - 0.5) * 0.85
    r, g, b = colorsys.hls_to_rgb(hue / 360.0, light, sat)
    return rgb_to_hex((r * 255, g * 255, b * 255))


def seed_to_palette(seed: str, chroma_override: float | None = None) -> dict[str, str]:
    """由种子色生成 M3 tonal palette（各色调下的一组色）。"""
    r, g, b = hex_to_rgb(seed)
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    hue = h * 360.0
    # 极暗色的 HLS 饱和度不可靠（近黑时 s 会虚高到 1.0），
    # 这里按绝对明度做上限约束，避免中性色被染成深蓝。
    chroma = s * 100.0
    if chroma_override is not None:
        chroma = chroma_override
    else:
        chroma = min(chroma, 100.0 * min(1.0, l * 2.2 + 0.12))

    pal: dict[str, str] = {}
    for tone in (0, 4, 6, 10, 12, 17, 20, 22, 24, 30, 40, 50, 60, 70, 80,
                 87, 90, 92, 94, 95, 96, 98, 99, 100):
        pal[f"t{tone}"] = _hct_tone_to_rgb(hue, chroma, tone)
    return pal


# ---------------------------------------------------------------------------
# 主题令牌
# ---------------------------------------------------------------------------

@dataclass
class Theme:
    name: str
    dark: bool
    colors: dict[str, str] = field(default_factory=dict)

    # 常用快捷访问，全部返回 hex 字符串，tkinter 可直接用
    def c(self, role: str) -> str:
        return self.colors.get(role) or self.colors["surface"]

    def rgb(self, role: str):
        return hex_to_rgb(self.c(role))

    def alpha(self, role: str, a: float, base: str = "surface") -> str:
        """带透明度的语义色叠在 base 上，用于 hover / ripple / 描边。"""
        return rgb_to_hex(over(self.rgb(role), self.rgb(base), a))

    # 形状（Android 17 Expressive：更大的圆角与形状变化）
    radius_xs = 6
    radius_sm = 10
    radius_md = 16
    radius_lg = 22
    radius_xl = 28
    radius_2xl = 34
    radius_full = 999

    # 间距（4dp 基准）
    space = (0, 4, 8, 12, 16, 20, 24, 32, 40, 48)

    # 动效曲线（Expressive spring 近似）
    @staticmethod
    def spring(t: float) -> float:
        """阻尼弹簧：快速起步、轻微过冲、平稳落定。"""
        if t <= 0:
            return 0.0
        if t >= 1:
            return 1.0
        return 1 - math.exp(-6.5 * t) * math.cos(9.0 * t)

    @staticmethod
    def decelerate(t: float) -> float:
        return 1 - (1 - t) ** 3

    @staticmethod
    def accelerate(t: float) -> float:
        return t ** 3


def _build(dark: bool, seed: str, neutral_seed: str | None = None) -> dict:
    primary = seed_to_palette(seed)
    # 中性色：沿用种子色的色相，但把彩度压到极低（M3 的 neutral 系列）
    pr, pg, pb = hex_to_rgb(seed)
    h, _, _ = colorsys.rgb_to_hls(pr / 255, pg / 255, pb / 255)
    neutral_src = rgb_to_hex(colorsys.hls_to_rgb(h, 0.5, 0.10))
    neutral = seed_to_palette(neutral_src, chroma_override=5.0)
    tertiary_h = (h * 360 + 60) % 360
    tertiary = seed_to_palette(_hct_tone_to_rgb(tertiary_h, 60, 50),
                               chroma_override=60.0)
    error = seed_to_palette("#B3261E", chroma_override=72.0)
    secondary = seed_to_palette(_hct_tone_to_rgb(h * 360, 40, 45),
                                chroma_override=22.0)

    c: dict[str, str] = {}
    if dark:
        c["primary"] = primary["t80"]
        c["on_primary"] = primary["t20"]
        c["primary_container"] = primary["t30"]
        c["on_primary_container"] = primary["t90"]
        c["secondary"] = secondary["t80"]
        c["secondary_container"] = secondary["t30"]
        c["on_secondary_container"] = secondary["t90"]
        c["tertiary"] = tertiary["t80"]
        c["tertiary_container"] = tertiary["t30"]
        c["on_tertiary_container"] = tertiary["t90"]
        c["error"] = error["t80"]
        c["on_error"] = error["t20"]
        c["error_container"] = error["t30"]
        c["on_error_container"] = error["t90"]
        c["surface"] = neutral["t6"]
        c["on_surface"] = neutral["t90"]
        c["surface_variant"] = neutral["t30"]
        c["on_surface_variant"] = neutral["t80"]
        c["surface_container_lowest"] = neutral["t4"]
        c["surface_container_low"] = neutral["t10"]
        c["surface_container"] = neutral["t12"]
        c["surface_container_high"] = neutral["t17"]
        c["surface_container_highest"] = neutral["t22"]
        c["outline"] = neutral["t70"]
        c["outline_variant"] = neutral["t40"]
        c["inverse_surface"] = neutral["t90"]
        c["inverse_on_surface"] = neutral["t20"]
        c["scrim"] = "#000000"
        c["success"] = "#6EDBA4"
        c["on_success"] = "#00391F"
        c["warning"] = "#FFD479"
        c["shadow"] = "#000000"
    else:
        c["primary"] = primary["t40"]
        c["on_primary"] = "#FFFFFF"
        c["primary_container"] = primary["t90"]
        c["on_primary_container"] = primary["t10"]
        c["secondary"] = secondary["t40"]
        c["secondary_container"] = secondary["t90"]
        c["on_secondary_container"] = secondary["t10"]
        c["tertiary"] = tertiary["t40"]
        c["tertiary_container"] = tertiary["t90"]
        c["on_tertiary_container"] = tertiary["t10"]
        c["error"] = error["t40"]
        c["on_error"] = "#FFFFFF"
        c["error_container"] = error["t90"]
        c["on_error_container"] = error["t10"]
        c["surface"] = neutral["t98"]
        c["on_surface"] = neutral["t10"]
        c["surface_variant"] = neutral["t90"]
        c["on_surface_variant"] = neutral["t30"]
        c["surface_container_lowest"] = "#FFFFFF"
        c["surface_container_low"] = neutral["t96"]
        c["surface_container"] = neutral["t94"]
        c["surface_container_high"] = neutral["t92"]
        c["surface_container_highest"] = neutral["t90"]
        c["outline"] = neutral["t50"]
        c["outline_variant"] = neutral["t80"]
        c["inverse_surface"] = neutral["t20"]
        c["inverse_on_surface"] = neutral["t95"]
        c["scrim"] = "#000000"
        c["success"] = "#1E8E5A"
        c["on_success"] = "#FFFFFF"
        c["warning"] = "#8A6100"
        c["shadow"] = "#3A3C40"
    return c


# Android 17 默认壁纸取色常用的几颗种子
SEEDS = {
    "青蓝": "#3D7BEA",
    "翡翠": "#00A87E",
    "紫罗兰": "#7C5CD6",
    "珊瑚": "#E05C5C",
    "琥珀": "#D98A00",
}


def make_theme(dark: bool = True, seed: str = "#3D7BEA") -> Theme:
    return Theme(name="dark" if dark else "light", dark=dark,
                 colors=_build(dark, seed))


# 语义色 → 中文名（设置页展示用）
ROLE_LABELS = {
    "primary": "主色",
    "primary_container": "主色容器",
    "secondary_container": "次色容器",
    "tertiary": "第三色",
    "surface": "表面",
    "surface_container_low": "低层容器",
    "surface_container": "容器",
    "surface_container_high": "高层容器",
    "on_surface": "正文",
    "on_surface_variant": "次要文本",
    "outline": "描边",
    "outline_variant": "分隔线",
    "error": "错误",
    "success": "成功",
}
