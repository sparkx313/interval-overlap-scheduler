"""interval-overlap-scheduler：三套区间冲突检测结构，同一套语义。

对外只暴露一个接口（insert / delete / overlaps / first_free / count_conflicts），
换结构不换调用代码，因此基准测到的差异只能来自算法本身。
"""

from __future__ import annotations

from .interval import (
    HALF_OPEN,
    INCLUSIVE,
    IntervalError,
    IntervalIndex,
    convention_for,
    make,
    overlap,
    sort_key,
)
from .naive import NaiveIndex
from .sweep import SweepIndex
from .tree import IntervalTree

__version__ = "0.1.0"

__all__ = [
    "HALF_OPEN",
    "INCLUSIVE",
    "IntervalError",
    "IntervalIndex",
    "IntervalTree",
    "NaiveIndex",
    "SweepIndex",
    "__version__",
    "convention_for",
    "make",
    "overlap",
    "sort_key",
]
