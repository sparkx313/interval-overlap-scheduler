"""固定种子的数据生成器：基准里的每个表格都必须在两次运行之间逐位复现。

约定：横轴 span 始终与区间个数 n 同比例放大（span = n * gap），
于是“负载密度”与规模解耦——第 1、2 节比的是表变大，第 6 节才专门动密度。
"""

from __future__ import annotations

import random

from .interval import make

DEFAULT_GAP = 400.0
DEFAULT_LENGTH = 120.0


def span_of(n, gap=DEFAULT_GAP):
    return n * gap


def build_table(seed, n, gap=DEFAULT_GAP, length=DEFAULT_LENGTH):
    """n 个随机起点、等长的区间，铺在 [0, n*gap) 上。"""
    rng = random.Random(seed)
    span = span_of(n, gap)
    out = []
    for i in range(n):
        start = rng.uniform(0.0, max(0.0, span - length))
        out.append(make(start, start + length, "t%d" % i))
    return out


def sorted_table(n, gap=DEFAULT_GAP, length=DEFAULT_LENGTH):
    """端点严格递增的表：有序输入，专门用来检验树会不会退化成链。"""
    span = span_of(n, gap)
    step = span / max(1.0, n)
    return [make(i * step, i * step + length, "s%d" % i) for i in range(n)]


def shuffled_table(seed, n, gap=DEFAULT_GAP, length=DEFAULT_LENGTH):
    """同一批端点、乱序插入：与 sorted_table 只差插入顺序，用于对照。"""
    rng = random.Random(seed)
    items = sorted_table(n, gap, length)
    order = list(range(n))
    rng.shuffle(order)
    out = []
    for rank, idx in enumerate(order):
        start = idx * (span_of(n, gap) / max(1.0, n))
        out.append(make(start, start + length, "h%d" % rank))
    return out


def build_queries(seed, m, n, gap=DEFAULT_GAP, length=DEFAULT_LENGTH):
    """m 条与表同尺度的短查询窗口（起点可落在表外）。"""
    rng = random.Random(seed)
    span = span_of(n, gap)
    out = []
    for i in range(m):
        start = rng.uniform(-length, span)
        out.append(make(start, start + length, "q%d" % i))
    return out


def density_queries(seed, m, n, fraction, gap=DEFAULT_GAP, length=DEFAULT_LENGTH):
    """窗口宽度按 fraction 缩放：fraction 越大，一次查询吐出的结果越多。"""
    span = span_of(n, gap)
    width = max(length, fraction * span)
    rng = random.Random(seed)
    out = []
    for i in range(m):
        start = rng.uniform(0.0, max(0.0, span - width))
        out.append(make(start, start + width, "d%d" % i))
    return out


def first_free_cases(seed, m, n, gap=DEFAULT_GAP, length=DEFAULT_LENGTH,
                     horizon_scale=1.0):
    """(start, duration_end, horizon) 三元组：时长固定为 length，horizon 拉满整张表。"""
    rng = random.Random(seed)
    span = span_of(n, gap)
    horizon = span * horizon_scale
    out = []
    for _ in range(m):
        start = rng.uniform(0.0, max(0.0, span * 0.8))
        out.append((start, start + length, horizon))
    return out


def boundary_cases():
    """边界样本：端点重合、零长度、同区间；三种结构必须给出同一答案。"""
    return [
        make(0, 10, "a"),
        make(10, 20, "b"),
        make(20, 20, "c"),
        make(5, 5, "d"),
        make(5, 15, "e"),
        make(0, 100, "long"),
        make(-30, -10, "early"),
        make(99, 100, "tail"),
        make(0, 10, "a"),
    ]


def boundary_queries():
    return [
        make(10, 20, "Q-touch-lo"),
        make(0, 5, "Q-inside"),
        make(5, 5, "Q-zero"),
        make(20, 20, "Q-zero-at-edge"),
        make(100, 140, "Q-at-end"),
        make(-40, -10, "Q-early"),
        make(0, 100, "Q-cover-all"),
        make(15, 25, "Q-touch-mid"),
        make(-1000, 1000, "Q-whole-world"),
    ]
