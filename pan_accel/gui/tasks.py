"""下载引擎适配层：把现有 CLI 引擎包装成 GUI 可用的、可观察的任务。

设计原则：
- 不修改 downloader.py / bt.py 的内部行为，只在外部观察进度；
- HTTP 下载通过 monkeypatch 分块写入点采集进度，避免侵入原实现；
- 所有回调都在工作线程里触发，GUI 侧负责切回主线程。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class TaskState(str, Enum):
    QUEUED = "排队中"
    PROBING = "探测中"
    DOWNLOADING = "下载中"
    VERIFYING = "校验中"
    DONE = "已完成"
    FAILED = "失败"
    PAUSED = "已暂停"
    CANCELLED = "已取消"


class TaskKind(str, Enum):
    HTTP = "HTTP 直链"
    THUNDER = "迅雷链接"
    BT = "BT / 磁力链"


@dataclass
class Task:
    """一个下载任务的可观察状态。"""

    tid: int
    kind: TaskKind
    source: str
    output: str = ""
    threads: int = 8
    chunk_mb: int = 32
    expect_md5: str = ""
    expect_sha1: str = ""

    state: TaskState = TaskState.QUEUED
    total_bytes: int = 0
    done_bytes: int = 0
    speed: float = 0.0          # bytes/s
    eta: float = 0.0            # seconds
    chunk_total: int = 0
    chunk_done: int = 0
    error: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    verified: bool | None = None

    # 运行期内部使用
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _worker: threading.Thread | None = field(default=None, repr=False)
    _speed_samples: list = field(default_factory=list, repr=False)

    @property
    def name(self) -> str:
        if self.output:
            return Path(self.output).name
        base = self.source.split("?")[0].rstrip("/")
        return base.rsplit("/", 1)[-1] or self.source[:40]

    @property
    def progress(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return max(0.0, min(1.0, self.done_bytes / self.total_bytes))

    @property
    def active(self) -> bool:
        return self.state in (TaskState.PROBING, TaskState.DOWNLOADING,
                              TaskState.VERIFYING, TaskState.QUEUED)

    def human_size(self, n: float) -> str:
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or unit == "TB":
                return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
            n /= 1024
        return f"{n:.1f} TB"

    def human_speed(self) -> str:
        return f"{self.human_size(self.speed)}/s" if self.speed else "—"

    def human_eta(self) -> str:
        if not self.eta or self.eta <= 0 or self.eta > 359999:
            return "—"
        s = int(self.eta)
        if s < 60:
            return f"{s} 秒"
        if s < 3600:
            return f"{s // 60} 分 {s % 60} 秒"
        return f"{s // 3600} 时 {(s % 3600) // 60} 分"


class SpeedMeter:
    """滑动窗口测速，避免瞬时抖动。"""

    def __init__(self, window=2.0):
        self.window = window
        self.samples: list[tuple[float, int]] = []

    def update(self, done_bytes: int, now: float | None = None) -> float:
        now = now or time.monotonic()
        self.samples.append((now, done_bytes))
        cutoff = now - self.window
        self.samples = [s for s in self.samples if s[0] >= cutoff]
        if len(self.samples) < 2:
            return 0.0
        t0, b0 = self.samples[0]
        t1, b1 = self.samples[-1]
        dt = t1 - t0
        if dt <= 0.05:
            return 0.0
        return max(0.0, (b1 - b0) / dt)

    def reset(self):
        self.samples.clear()
