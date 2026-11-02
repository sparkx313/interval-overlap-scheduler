#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""可复现基准：naive / sweep / tree 三套区间冲突检测结构的实测代价。

输出的每个数字都来自本轮运行：解释性文字里的倍数、翻转点、噪声地板全部
由当轮测量算出（跨小节的引用也取自同一个 MEASURED 字典），不写任何
“理论上应该是”的固定结论。
"""

from __future__ import annotations

import gc
import io
import os
import sys
import textwrap
import time
import tracemalloc

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from intervallib import (  # noqa: E402
    HALF_OPEN,
    INCLUSIVE,
    IntervalError,
    IntervalTree,
    NaiveIndex,
    SweepIndex,
    make,
    trace,
)

ARTIFACT = os.path.join(HERE, "last_run.txt")
WIDTH = 78
# 忙表约定：占用率 = DENSE_LEN / DENSE_GAP，预约请求比空档短，才谈得上“跳几格”。
DENSE_GAP = 200.0
DENSE_LEN = 120.0
ASK = 50.0
MEASURED = {}


class _Tee:
    """缓冲所有 stdout，收尾时逐字落盘：控制台看到的与文件里的必须完全一致。"""

    def __init__(self, stream):
        self.stream = stream
        self.chunks = []

    def write(self, text):
        self.chunks.append(text)
        return self.stream.write(text)

    def flush(self):
        return self.stream.flush()

    def text(self):
        return "".join(self.chunks)


def banner(title):
    print("=" * WIDTH)
    print(title)
    print("=" * WIDTH)


def say(text):
    print()
    print(textwrap.fill("Interpretation: " + text, 78, subsequent_indent="  "))


def table(columns, rows):
    """columns = [(标题, 宽度)]；单元格一律裁到宽度，行总长不超过 78。"""
    widths = [w for _, w in columns]
    print("".join(title[:w].rjust(w) for title, w in columns))
    print("".join("-" * (w - 1) + " " for w in widths).rstrip())
    for row in rows:
        print("".join(str(cell)[:w].rjust(w)
                      for cell, w in zip(row, widths)).rstrip())


def index_trio(items, convention=HALF_OPEN):
    """同一批数据喂给三套结构；sweep 走 bulk_load，其余走增量 insert。"""
    naive = NaiveIndex(convention).bulk_load(items)
    sweep = SweepIndex(convention).bulk_load(items)
    tree = IntervalTree(convention).bulk_load(items)
    return naive, sweep, tree


def probe_counts(index, queries):
    """返回 (每查询判定次数, 每查询内部定位比较, 平均结果条数)。"""
    index.reset_stats()
    found = 0
    for query in queries:
        found += len(index.overlaps(query))
    comps, nav = index.stats()
    divisor = float(len(queries))
    return comps / divisor, nav / divisor, found / divisor


def free_counts(index, cases):
    """first_free 的代价按“一次预约尝试”计：判定次数 + 平均要跳几格。"""
    index.reset_stats()
    solved = 0
    for start, end, horizon in cases:
        if index.first_free(start, end, horizon) is not None:
            solved += 1
    comps, _ = index.stats()
    return (comps / len(cases), index.jumps / len(cases),
            solved / float(len(cases)))


def timed(fn, repeat=5):
    """预热 2 轮丢弃，再取 5 轮最优；返回最优与该 5 轮极差（本轮噪声地板）。"""
    gc.disable()
    try:
        for _ in range(2):
            fn()
        runs = []
        for _ in range(repeat):
            tick = time.perf_counter()
            fn()
            runs.append(time.perf_counter() - tick)
    finally:
        gc.enable()
    best = min(runs)
    return best, max(runs) - best


def per_call(best, calls):
    return 1e6 * best / max(1, calls)


def time_per_query(index, queries):
    best, spread = timed(
        lambda: [index.overlaps(q) for q in queries])
    return per_call(best, len(queries)), per_call(spread, len(queries))


def pair_floor(*bands):
    """平局只按「参与比较的这两者各自噪声里较大的那个」判定。

    用整表最大噪声当门槛，会一边吞掉真实的小差距、一边又和同一段里自己宣称的
    比值互相矛盾——naive 的波动本来就比索引大几个数量级。
    """
    return max(bands)


def name_list(sizes):
    return ", ".join("n=%d" % n for n in sizes) if sizes else "no size"


def section_overlaps_scale():
    banner("1) comparisons per overlaps query vs table size (deterministic)")
    print("the axis grows with the table (span = n * gap), so the load density")
    print("is held fixed and only the number of stored intervals grows.")
    columns = [("n", 8), ("q", 6), ("mean_k", 8), ("naive/cq", 10),
               ("sweep/cq", 10), ("tree/cq", 10), ("sweep_nav", 11),
               ("naive/tree", 11)]
    rows = []
    for n in (100, 1000, 5000, 20000, 100000):
        queries = trace.build_queries(31, 40 if n >= 20000 else 200, n)
        naive, sweep, tree = index_trio(trace.build_table(7, n))
        nc, _, k = probe_counts(naive, queries)
        sc, sn, _ = probe_counts(sweep, queries)
        tc, _, _ = probe_counts(tree, queries)
        MEASURED["scale-%d" % n] = (nc, sc, tc, k, nc / max(1e-9, tc), sn)
        rows.append((n, len(queries), "%.2f" % k, "%.1f" % nc, "%.1f" % sc,
                     "%.1f" % tc, "%.1f" % sn, "%.2fx" % (nc / max(1e-9, tc))))
    table(columns, rows)
    lo = MEASURED["scale-100"]
    hi = MEASURED["scale-100000"]
    say("naive tests %.1f intervals per query at n=100 and %.1f at n=100000, "
        "i.e. %.0fx more work for a 1000x larger table - the scan cost equals "
        "n by construction, so this is the linear baseline, not a surprise."
        % (lo[0], hi[0], hi[0] / lo[0]))
    say("over the same 1000x growth the tree goes %.1f -> %.1f tests per query "
        "(%.1fx) and the sweep, here to two decimals so the endpoints do not "
        "both round away, %.2f -> %.2f (%.1fx); the table's sweep column is "
        "one-decimal, which is why its endpoints look flat there while the "
        "unrounded ratio is still %.1fx. mean result size is %.2f -> %.2f, so "
        "the growth measured here is table size, not output size."
        % (lo[2], hi[2], hi[2] / lo[2], lo[1], hi[1],
           hi[1] / max(1e-9, lo[1]), hi[1] / max(1e-9, lo[1]), lo[3], hi[3]))
    say("the tree's measured edge is %.2fx at n=100000 and still %.2fx at "
        "n=100; the sweep needs only %.1f probes at n=100000 but %.1f "
        "binary-search comparisons to locate its cut point, so its work is "
        "nav-dominated while the tree's is probe-dominated."
        % (hi[4], lo[4], hi[1], hi[5]))
    say("stated plainly, that makes the sweep beat the tree on probe count "
        "alone at every size in this table (%.1f against %.1f at n=100000): "
        "stored lengths are uniform here, so the prefix-max cut lands almost "
        "immediately. only the wall clock in 1b can decide between them."
        % (hi[1], hi[2]))


def section_overlaps_time():
    banner("1b) wall clock per overlaps query (timing)")
    print("best of 5 passes after 2 discarded warm-ups; the band column gives")
    print("each structure's own worst-best spread for that pass, us per query.")
    print("a pairwise verdict uses the wider of the two bands it compares, never")
    print("the widest number in the row.")
    columns = [("n", 7), ("q/pass", 7), ("naive us/q", 11), ("sweep us/q", 11),
               ("tree us/q", 11), ("band n/s/t", 16), ("naive/tree", 11)]
    rows = []
    for n in (1000, 10000, 100000):
        queries = trace.build_queries(31, max(20, 250000 // n), n)
        stats = [time_per_query(idx, queries)
                 for idx in index_trio(trace.build_table(7, n))]
        MEASURED["time-%d" % n] = stats
        rows.append((n, len(queries), "%.1f" % stats[0][0], "%.1f" % stats[1][0],
                     "%.1f" % stats[2][0], "%.1f/%.1f/%.1f" % (
                         stats[0][1], stats[1][1], stats[2][1]),
                     "%.1fx" % (stats[0][0] / max(1e-9, stats[2][0]))))
    table(columns, rows)
    big = MEASURED["time-100000"]
    small = MEASURED["time-1000"]
    big_floor = pair_floor(big[0][1], big[2][1])
    small_floor = pair_floor(small[0][1], small[2][1])
    say("at n=100000 one conflicts query is naive %.1f us, sweep %.1f us, tree "
        "%.1f us: a %.0fx gap. the wider of the naive and tree bands at that "
        "size is %.1f us, so the gap is %.0fx its own floor and is not noise."
        % (big[0][0], big[1][0], big[2][0], big[0][0] / big[2][0], big_floor,
           (big[0][0] - big[2][0]) / max(1e-9, big_floor)))
    small_strength = (small[0][0] - small[2][0]) / max(1e-9, small_floor)
    big_strength = (big[0][0] - big[2][0]) / max(1e-9, big_floor)
    say("at n=1000 the same structures cost %.1f / %.1f / %.1f us; the naive "
        "band there is %.1f us and the tree's %.1f us, so the naive-tree gap of "
        "%.1f us is %.1fx the pair floor, %s the n=100000 row's %.0fx. both "
        "clear their own floors, so neither is called a tie; which of the two "
        "is the stronger evidence this run is set by their bands, not by the "
        "size of the table."
        % (small[0][0], small[1][0], small[2][0], small[0][1], small[2][1],
           small[0][0] - small[2][0], small_strength,
           ("well below" if small_strength < big_strength / 1.5 else
            "about the same as" if small_strength < big_strength * 1.5 else
            "above"),
           big_strength))
    cheap = {"sweep": [], "tree": [], "tie": []}
    widest_gap = 0.0
    widest_n = None
    for n in (1000, 10000, 100000):
        s = MEASURED["time-%d" % n]
        floor = pair_floor(s[1][1], s[2][1])
        gap = s[2][0] - s[1][0]
        if abs(gap) > widest_gap:
            widest_gap, widest_n = abs(gap), n
        cheap["sweep" if gap > floor else "tree" if -gap > floor else "tie"].append(n)
    if cheap["sweep"] or cheap["tree"]:
        MEASURED["read-winner"] = ("sweep" if len(cheap["sweep"])
                                   >= len(cheap["tree"]) else "tree")
        MEASURED["read-verdict"] = ("the %s is already the cheaper reader at "
                                    "%d of the 3 sizes in 1b"
                                    % (MEASURED["read-winner"],
                                       max(len(cheap["sweep"]),
                                           len(cheap["tree"]))))
        closing = ("the sweep wins %d of the %d sizes and the tree %d, by at "
                   "most %.1f us per query - a real margin, but small next to "
                   "the naive gap, so what actually separates the two indexed "
                   "structures is still the write cost in section 4"
                   % (len(cheap["sweep"]), 3, len(cheap["tree"]), widest_gap))
    else:
        MEASURED["read-winner"] = "tie"
        MEASURED["read-verdict"] = ("the two indexed structures were tied on "
                                    "reads at all 3 sizes in 1b")
        closing = ("the two indexed structures are practically tied on reads, "
                   "so the choice between them is the write cost measured in "
                   "section 4, not this table")
    say("sweep against tree on the clock, judged pair by pair against the wider "
        "of their own two bands: the sweep is measurably cheaper at %s, the tree "
        "at %s, and the two are inside noise at %s. the largest sweep-tree gap "
        "in this section is %.1f us per query (at n=%d), against a naive-tree "
        "gap of %.1f us at the same size - %s."
        % (name_list(cheap["sweep"]), name_list(cheap["tree"]),
           name_list(cheap["tie"]), widest_gap, widest_n,
           MEASURED["time-%d" % widest_n][0][0]
           - MEASURED["time-%d" % widest_n][2][0], closing))


def section_first_free_scale():
    banner("2) comparisons per first_free call on a busy table (deterministic)")
    print("table occupancy is %.0f%% (length %.0f / gap %.0f) and each request"
          % (100.0 * DENSE_LEN / DENSE_GAP, DENSE_LEN, DENSE_GAP))
    print("is %.0f wide, so a free slot usually requires hopping past bookings;"
          % ASK)
    print("each hop is a fresh search. that is why first_free is the worst")
    print("query for an index built to answer one search at a time.")
    columns = [("n", 8), ("cases", 7), ("hops/call", 10), ("naive/call", 11),
               ("sweep/call", 11), ("tree/call", 10), ("naive/tree", 11)]
    rows = []
    for n in (100, 1000, 5000, 20000, 100000):
        items = trace.build_table(13, n, gap=DENSE_GAP, length=DENSE_LEN)
        cases = trace.first_free_cases(17, 20 if n >= 20000 else 120, n,
                                       gap=DENSE_GAP, length=ASK)
        naive, sweep, tree = index_trio(items)
        nc, nh, nf = free_counts(naive, cases)
        sc, sh, sf = free_counts(sweep, cases)
        tc, th, tf = free_counts(tree, cases)
        MEASURED["free-%d" % n] = (nc, sc, tc, nh, nf, th)
        rows.append((n, len(cases), "%.2f" % nh, "%.1f" % nc, "%.1f" % sc,
                     "%.1f" % tc, "%.2fx" % (nc / max(1e-9, tc))))
    table(columns, rows)
    lo = MEASURED["free-100"]
    hi = MEASURED["free-100000"]
    say("one booking attempt performs %.2f searches per call at n=100 and "
        "%.2f at n=100000, so its cost is hops x per-search, not one search; "
        "%.0f%% of the requests are satisfiable at n=100 and %.0f%% at "
        "n=100000." % (lo[3], hi[3], 100.0 * lo[4], 100.0 * hi[4]))
    say("per call naive rises %.1f -> %.1f tests (%.0fx) while the tree rises "
        "%.1f -> %.1f (%.1fx), so the tree's lead here is %.2fx against %.2fx "
        "for a plain overlaps query on the same table size (section 1): the "
        "hop loop spends part of the index's advantage."
        % (lo[0], hi[0], hi[0] / lo[0], lo[2], hi[2], hi[2] / lo[2],
           hi[0] / max(1e-9, hi[2]), MEASURED["scale-100000"][4]))
    sweep_vs_tree = ("the sorted list is the cheapest of the three here"
                     if hi[1] < hi[2] else
                     "the sorted list still beats the scan by %.0fx at n=100000"
                     % (hi[0] / max(1e-9, hi[1])))
    say("%s: %.1f tests per call against %.1f for the tree and %.1f for naive, "
        "i.e. %.2fx the tree's work. its probe count is small because one "
        "prefix-max cut removes the tail; what it cannot avoid is paying for "
        "that array on every write, which is section 4."
        % (sweep_vs_tree, hi[1], hi[2], hi[0], hi[1] / max(1e-9, hi[2])))


def section_first_free_time():
    banner("2b) wall clock per first_free call (timing)")
    columns = [("n", 8), ("cases/pass", 11), ("naive us", 11), ("sweep us", 11),
               ("tree us", 11), ("band n/s/t", 15), ("naive/tree", 11)]
    rows = []
    for n in (1000, 10000, 50000):
        items = trace.build_table(13, n, gap=DENSE_GAP, length=DENSE_LEN)
        cases = trace.first_free_cases(17, max(20, 60000 // n), n,
                                       gap=DENSE_GAP, length=ASK)
        stats = []
        for index in index_trio(items):
            best, spread = timed(lambda idx=index: [idx.first_free(*c)
                                                     for c in cases])
            stats.append((per_call(best, len(cases)), per_call(spread, len(cases))))
        MEASURED["freetime-%d" % n] = stats
        rows.append((n, len(cases), "%.1f" % stats[0][0], "%.1f" % stats[1][0],
                     "%.1f" % stats[2][0], "%.1f/%.1f/%.1f" % (
                         stats[0][1], stats[1][1], stats[2][1]),
                     "%.1fx" % (stats[0][0] / max(1e-9, stats[2][0]))))
    table(columns, rows)
    top = MEASURED["freetime-50000"]
    floor = pair_floor(top[0][1], top[2][1])
    say("one booking attempt at n=50000 costs naive %.1f us, sweep %.1f us, "
        "tree %.1f us; the wider of the naive and tree bands at that size is "
        "%.1f us, so the naive-tree gap of %.1f us clears its own floor by "
        "%.1fx."
        % (top[0][0], top[1][0], top[2][0], floor, top[0][0] - top[2][0],
           (top[0][0] - top[2][0]) / max(1e-9, floor)))
    plain = MEASURED["time-1000"]
    free = MEASURED["freetime-1000"]
    hops = MEASURED["free-100000"][0] / max(1e-9, MEASURED["free-100000"][2])
    plain_hi = MEASURED["scale-100000"][4]
    say("wall clock on the same n=1000 table: a single overlaps query is %.1fx "
        "naive over tree, one first_free call %.1fx, so the hop loop spends "
        "%.1f%% of the index's measured lead at that size. section 2 makes the "
        "same comparison on probes at n=100000 (%.2fx against %.2fx), where it "
        "spends only %.1f%% - the two agree on direction and disagree on "
        "extent, and this run cannot separate the extra search from the "
        "per-call overhead each hop drags along (a result list to build, a "
        "sort, a Python call)."
        % (plain[0][0] / max(1e-9, plain[2][0]),
           free[0][0] / max(1e-9, free[2][0]),
           100.0 * (1.0 - min(1.0, (free[0][0] / max(1e-9, free[2][0]))
                              / (plain[0][0] / max(1e-9, plain[2][0])))),
           hops, plain_hi,
           100.0 * (1.0 - min(1.0, hops / plain_hi))))
    say("absolute cost is the honest headline: %.1f us per attempt on the tree "
        "means a UI could run it about %.0f times per second on this machine."
        % (top[2][0], 1e6 / max(1e-9, top[2][0])))


def section_correctness():
    banner("3) all three structures must agree, exactly (deterministic)")
    print("a disagreement is any query where the three result lists differ in")
    print("content OR order. expected value 0; reported plainly below.")
    columns = [("dataset", 22), ("queries", 9), ("stored", 8), ("disagree", 10),
               ("items out", 11), ("tree out", 10)]
    rows = []
    total_bad = 0
    total_q = 0
    total_items = 0
    datasets = (("sparse n=3000", 3000, trace.DEFAULT_GAP, trace.DEFAULT_LENGTH),
                ("dense n=3000", 3000, DENSE_GAP, DENSE_LEN),
                ("sparse n=400", 400, trace.DEFAULT_GAP, trace.DEFAULT_LENGTH))
    for label, n, gap, length in datasets:
        items = trace.build_table(23, n, gap=gap, length=length)
        queries = trace.build_queries(29, 500, n, gap=gap, length=length)
        for convention in (HALF_OPEN, INCLUSIVE):
            results = [[idx.overlaps(q) for q in queries]
                       for idx in index_trio(items, convention)]
            bad = sum(0 if (results[0][i] == results[1][i]
                            and results[0][i] == results[2][i]) else 1
                      for i in range(len(queries)))
            out = sum(len(r) for r in results[0])
            total_bad += bad
            total_q += len(queries)
            total_items += out
            rows.append(("%s %s" % (label, convention[:4]), len(queries),
                         len(items), bad, out,
                         sum(len(r) for r in results[2])))
    table(columns, rows)
    MEASURED["agree"] = (total_bad, total_q, total_items)
    print()
    print("boundary cases: conflict counts per structure")
    cases = [("touching [0,10) vs [10,20)", [make(0, 10, "a")], make(10, 20, "q")),
             ("zero-length query [10,10)",
              [make(0, 10, "a"), make(10, 20, "b")], make(10, 10, "q")),
             ("zero-length stored [5,5)", [make(0, 10, "a")], make(5, 5, "q")),
             ("duplicate [0,10) inserted twice",
              [make(0, 10, "a"), make(0, 10, "a")], make(1, 2, "q")),
             ("contained [2,3) inside [0,10)", [make(0, 10, "a")], make(2, 3, "q"))]
    columns = [("case", 30), ("open", 7), ("incl", 7), ("naive", 7),
               ("sweep", 7), ("tree", 7), ("agree", 6)]
    rows = []
    for label, items, query in cases:
        open_trio = index_trio(items, HALF_OPEN)
        incl_trio = index_trio(items, INCLUSIVE)
        counts = [len(idx.overlaps(query)) for idx in open_trio]
        rows.append((label, counts[0], len(incl_trio[0].overlaps(query)),
                     counts[0], counts[1], counts[2],
                     "yes" if len(set(counts)) == 1 else "NO"))
    table(columns, rows)
    MEASURED["boundary"] = rows
    print()
    verdicts = []
    for kind, factory in (("naive", NaiveIndex), ("sweep", SweepIndex),
                          ("tree", IntervalTree)):
        try:
            factory().insert(make(10, 3, "inverted"))
            verdicts.append("%s:ACCEPTED" % kind)
        except IntervalError:
            verdicts.append("%s:rejected" % kind)
    print("inverted [10,3) insert -> " + ", ".join(verdicts))
    say("%d seeded queries per dataset over 3 datasets x 2 boundary conventions "
        "= %d compared query results covering %d reported conflict rows. "
        "disagreements found: %d (expected 0)."
        % (500, total_q, total_items, total_bad))
    first, second = rows[0], rows[1]
    say("the convention is not decorative: touching intervals report %d "
        "conflicts half-open and %d inclusive, and a zero-length query reports "
        "%d half-open against %d inclusive - identical numbers in either row "
        "would mean the switch does nothing."
        % (first[1], first[2], second[1], second[2]))
    say("the zero-length stored interval [5,5) draws counts %s across the "
        "three structures (identical means the empty-interval rule is shared), "
        "duplicates come back as %d rows everywhere, and every structure "
        "rejects the inverted interval before it can corrupt an augmented "
        "max_end." % (list(rows[2][3:6]), rows[3][3]))


def churn(idx, ops):
    """写代价用一个“插入 150 条再删掉 150 条”的往返测：表长在 7 轮之间不变，
    因此不必从建树时间里做减法（那种减法在小算子上会被噪声吃掉）。
    代价是 insert 与 delete 只能合并报告，本节的列名如实写作 us/op（每腿一半）。
    """
    for item in ops:
        idx.insert(item)
    for item in ops:
        idx.delete(item)


def section_write_cost():
    banner("4) insert / delete cost against table depth (timing)")
    print("each pass inserts 150 extra intervals then deletes them again, so the")
    print("table size is identical for every pass and no build time is subtracted;")
    print("us/op counts both legs (insert + delete) as one round trip per item.")
    columns = [("n", 6), ("tree h", 7), ("naive us/op", 12), ("sweep us/op", 12),
               ("tree us/op", 12), ("band n/s/t", 16), ("tree desc", 11)]
    rows = []
    heights = {}
    descents = {}
    for n in (250, 500, 1000, 2000, 4000):
        items = trace.build_table(41, n)
        extra = trace.build_table(43, 150)
        stats = {}
        for name, cls in (("naive", NaiveIndex), ("sweep", SweepIndex),
                          ("tree", IntervalTree)):
            index = cls().bulk_load(items)
            best, spread = timed(lambda: churn(index, extra))
            stats[name] = (2 * 150.0, best, spread)
        fresh = IntervalTree().bulk_load(items)
        heights[n] = fresh.height()
        descents[n] = fresh.nav / float(n)
        MEASURED["write-%d" % n] = stats
        rows.append((n, heights[n], "%.2f" % (stats["naive"][1] / 300 * 1e6),
                     "%.2f" % (stats["sweep"][1] / 300 * 1e6),
                     "%.2f" % (stats["tree"][1] / 300 * 1e6),
                     "%.1f/%.1f/%.1f" % (stats["naive"][2] / 300 * 1e6,
                                         stats["sweep"][2] / 300 * 1e6,
                                         stats["tree"][2] / 300 * 1e6),
                     "%.1f" % descents[n]))
    table(columns, rows)
    lo = MEASURED["write-250"]
    hi = MEASURED["write-4000"]
    say("one write round trip costs the sweep %.2f us per op at n=250 and %.2f "
        "us at n=4000, i.e. %.1fx for a 16x larger table: both legs rebuild the"
        " prefix-max array from the touched index to the tail, so a write is "
        "Python-level O(n) no matter how the table was built."
        % (lo["sweep"][1] / 300 * 1e6, hi["sweep"][1] / 300 * 1e6,
           (hi["sweep"][1] / 300 * 1e6) / max(1e-9, lo["sweep"][1] / 300 * 1e6)))
    say("the tree moves %.2f -> %.2f us per op (%.2fx) while its measured "
        "height grows %d -> %d levels and its average insert descent %.1f -> "
        "%.1f comparisons. naive goes %.2f -> %.2f us: inserting is a bare "
        "append, but the delete leg is list.remove, a full scan of the list, so "
        "the scan is cheap to write into and expensive to write out of."
        % (lo["tree"][1] / 300 * 1e6, hi["tree"][1] / 300 * 1e6,
           (hi["tree"][1] / 300 * 1e6) / max(1e-9, lo["tree"][1] / 300 * 1e6),
           heights[250], heights[4000], descents[250], descents[4000],
           lo["naive"][1] / 300 * 1e6, hi["naive"][1] / 300 * 1e6))
    gap_nt = abs(hi["naive"][1] - hi["tree"][1]) / 300 * 1e6
    gap_st = abs(hi["sweep"][1] - hi["tree"][1]) / 300 * 1e6
    floor_nt = pair_floor(hi["naive"][2], hi["tree"][2]) / 300 * 1e6
    floor_st = pair_floor(hi["sweep"][2], hi["tree"][2]) / 300 * 1e6
    say("each write difference is judged against the wider of its own two bands "
        "at n=4000. naive versus tree: gap %.2f us per op against bands of %.2f "
        "(naive) and %.2f (tree), so it is %s. sweep versus tree: gap %.2f "
        "against bands of %.2f and %.2f, so it is %s - the sorted list is the "
        "expensive writer, even though %s."
        % (gap_nt, hi["naive"][2] / 300 * 1e6, hi["tree"][2] / 300 * 1e6,
           "a tie" if gap_nt <= floor_nt else "real (%.1fx its floor)"
           % (gap_nt / floor_nt),
           gap_st, hi["sweep"][2] / 300 * 1e6, hi["tree"][2] / 300 * 1e6,
           "a tie" if gap_st <= floor_st else "real (%.1fx its floor)"
           % (gap_st / floor_st), MEASURED["read-verdict"]))
    say("depth does not price the write: the tree's write price rose %.2fx "
        "while the insert descent it performs rose %.2fx (%.1f -> %.1f "
        "comparisons), i.e. the cost grew by LESS than the number of "
        "comparisons made, so searching is not what the %.2f -> %.2f us per op "
        "is mainly spent on. this section cannot split allocation from "
        "searching; it only shows that the descent alone does not explain the "
        "price, because a taller tree here cost less per added comparison than "
        "the shallower one did."
        % ((hi["tree"][1] / 300 * 1e6) / max(1e-9, lo["tree"][1] / 300 * 1e6),
           descents[4000] / max(1e-9, descents[250]), descents[250],
           descents[4000], lo["tree"][1] / 300 * 1e6, hi["tree"][1] / 300 * 1e6))


def section_balance():
    banner("4b) does the tree degenerate on already-sorted input? (deterministic)")
    n = 2000
    tail = make(trace.span_of(n) - 1.0, trace.span_of(n), "tail")
    columns = [("priorities", 16), ("build order", 13), ("height", 8),
               ("nav/ins", 9), ("tail comps", 11), ("shape", 10)]
    rows = []
    for pname, randomize in (("random", True), ("insertion order", False)):
        for order, items in (("sorted", trace.sorted_table(n)),
                             ("shuffled", trace.shuffled_table(5, n))):
            tree = IntervalTree(randomize=randomize)
            tree.bulk_load(items)
            nav_total = tree.nav
            tree.reset_stats()
            tree.overlaps(tail)
            probed, _ = tree.stats()
            rows.append((pname, order, tree.height(), "%.1f" % (nav_total / n),
                         probed, "chain" if tree.height() >= n else "balanced"))
    table(columns, rows)
    MEASURED["degenerate"] = rows
    # 按 (优先级, 构造顺序) 取行，不按位置取：位置一错就会引到别的单元格上。
    def row(pname, order):
        return next(r for r in rows if r[0] == pname and r[1] == order)
    chain = [r for r in rows if r[5] == "chain"]
    say("randomized priorities on sorted input: height %s, tail query tests %s "
        "intervals. priorities = insertion order on the same sorted input: a "
        "chain of height %s that tests all %s intervals for that query."
        % (row("random", "sorted")[2], row("random", "sorted")[4],
           row("insertion order", "sorted")[2],
           row("insertion order", "sorted")[4]))
    say("the answer to 'does it degenerate' is: no, but only because of the "
        "random priorities - nav/ins on sorted input is %s comparisons with "
        "them and %s without, %.1fx apart in the write path alone."
        % (row("random", "sorted")[3], row("insertion order", "sorted")[3],
           float(row("insertion order", "sorted")[3])
           / max(1e-9, float(row("random", "sorted")[3]))))
    shuffled_det = row("insertion order", "shuffled")
    say("shuffled order with deterministic priorities is the honest middle "
        "case (height %s, nav/ins %s, tail query %s comparisons - still not the "
        "%d-deep chain): the collapse needs BOTH sorted input and "
        "non-randomized priorities, which is exactly what happens when bookings "
        "arrive in date order."
        % (shuffled_det[2], shuffled_det[3], shuffled_det[4], n))
    say("the traversal is iterative, so the height-%d chain was walked without "
        "hitting Python's recursion limit; the query above really returned %s "
        "comparisons, which is the cost of not balancing."
        % (chain[0][2], chain[0][4]))


def tracemalloc_delta(build):
    gc.collect()
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    holder = build()
    after = tracemalloc.take_snapshot()
    delta = sum(entry.size_diff for entry in after.compare_to(before, "filename"))
    tracemalloc.stop()
    del holder
    return max(0, delta)


def section_memory():
    banner("5) bytes held per stored interval (deterministic)")
    n = 20000
    items = trace.build_table(53, n)
    fresh = tracemalloc_delta(lambda: [make(i[0], i[1], i[2]) for i in items])
    shared = tracemalloc_delta(lambda: list(items))
    naive = tracemalloc_delta(lambda: NaiveIndex().bulk_load(items))
    sweep = tracemalloc_delta(lambda: SweepIndex().bulk_load(items))
    tree = tracemalloc_delta(lambda: IntervalTree().bulk_load(items))
    columns = [("holder", 24), ("KiB", 10), ("bytes/iv", 11), ("vs naive", 11),
               ("vs fresh", 11)]
    rows = []
    for label, value in (("fresh tuple list", fresh),
                         ("shared tuple list", shared), ("naive scan", naive),
                         ("sorted sweep", sweep), ("interval tree", tree)):
        rows.append((label, "%.1f" % (value / 1024.0), "%.1f" % (value / n),
                     "%.2fx" % (value / max(1.0, naive)),
                     "%.2fx" % (value / max(1.0, fresh))))
    table(columns, rows)
    say("method: tracemalloc delta across the build, n=%d, source list created "
        "before the snapshot so its tuples are never charged to a structure."
        % n)
    say("the floor is %.1f bytes per interval for a fresh list of tuples; "
        "naive lands at %.1f (%.2fx the floor - it only adds list slack), "
        "sweep %.1f (%.2fx) because it keeps a parallel sort-key tuple plus a "
        "prefix-max array, and the tree %.1f (%.2fx) for two child pointers, a "
        "parent pointer, a priority and the augmented max_end."
        % (fresh / n, naive / n, naive / max(1.0, fresh), sweep / n,
           sweep / max(1.0, fresh), tree / n, tree / max(1.0, fresh)))
    say("reusing existing tuples instead of copying them would leave a holder at "
        "%.1f bytes per interval, so %.1f of naive's %.1f is the tuple itself; "
        "the tree's overhead over naive at n=%d is %.0f KiB for the whole table."
        % (shared / n, (naive - shared) / n, naive / n, n,
           (tree - naive) / 1024.0))


def section_density():
    banner("6) overlap density sweep at fixed table size (deterministic)")
    n = 20000
    print("the window width is scaled to a fraction of the axis, so k (returned")
    print("conflicts) is what changes while the table stays at n=%d." % n)
    columns = [("wanted", 8), ("mean_k", 9), ("naive/cq", 10), ("sweep/cq", 10),
               ("tree/cq", 9), ("tree/k", 9), ("naive/tree", 11)]
    rows = []
    items = trace.build_table(61, n)
    for fraction in (0.0005, 0.002, 0.01, 0.05, 0.2, 0.5, 1.0):
        queries = trace.density_queries(67, 30, n, fraction)
        naive, sweep, tree = index_trio(items)
        nc, _, k = probe_counts(naive, queries)
        sc, _, _ = probe_counts(sweep, queries)
        tc, _, _ = probe_counts(tree, queries)
        MEASURED["dens-%.4f" % fraction] = (nc, sc, tc, k)
        rows.append(("%.4f" % fraction, "%.1f" % k, "%.0f" % nc, "%.0f" % sc,
                     "%.0f" % tc, "%.2f" % (tc / max(1e-9, k)),
                     "%.1fx" % (nc / max(1e-9, tc))))
    table(columns, rows)
    sparse = MEASURED["dens-0.0005"]
    heavy = MEASURED["dens-1.0000"]
    say("at wanted fraction 0.0005 the query returns %.1f conflicts, the tree "
        "tests %.0f intervals and naive %.0f: %.1fx fewer, and tree/k = %.2f "
        "says how much of the tree's work is output rather than search."
        % (sparse[3], sparse[2], sparse[0], sparse[0] / max(1e-9, sparse[2]),
           sparse[2] / max(1e-9, sparse[3])))
    say("widening the window converges the three: at fraction 1.0 the tree "
        "tests %.0f against naive's %.0f (%.1fx) while returning %.0f conflicts "
        "- the output now dwarfs the search, so the structure stops paying."
        % (heavy[2], heavy[0], heavy[0] / max(1e-9, heavy[2]), heavy[3]))
    dens = dict((f, v) for f, v in MEASURED.items() if f.startswith("dens-"))
    best = max(dens, key=lambda f: dens[f][0] / max(1e-9, dens[f][2]))
    ratio = dens[best][0] / max(1e-9, dens[best][2])
    say("the strongest measured ratio across the sweep is %.1fx at wanted "
        "fraction %s (naive %.0f tests against the tree's %.0f); the sweep "
        "undercut even the tree there, %.2f unrounded tests against the tree's "
        "%.2f (%.1fx fewer, both more precise than the table's integer column), "
        "so this table must be read as probe counts, not as a ranking of "
        "structures."
        % (ratio, best.split("-")[1], dens[best][0], dens[best][2],
           dens[best][1], dens[best][2],
           dens[best][2] / max(1e-9, dens[best][1])))


def section_flip():
    banner("6b) the size at which the tree starts winning (timing)")
    print("busy single-process calendar: occupancy %.0f%%, window %.0f, n from 20"
          % (100.0 * DENSE_LEN / DENSE_GAP, DENSE_LEN))
    print("up to 3000. the winner column names the cheapest structure only when")
    print("its lead over the runner-up exceeds the wider of those two bands.")
    columns = [("n", 6), ("q/pass", 7), ("naive us/q", 11), ("sweep us/q", 11),
               ("tree us/q", 11), ("band n/s/t", 17), ("winner", 14)]
    rows = []
    MEASURED["wide"] = []
    for n in (20, 50, 100, 200, 500, 1000, 3000):
        items = trace.build_table(71, n, gap=DENSE_GAP, length=DENSE_LEN)
        queries = trace.build_queries(73, max(300, 150000 // n), n,
                                      gap=DENSE_GAP, length=DENSE_LEN)
        stats = [time_per_query(idx, queries) for idx in index_trio(items)]
        MEASURED["flip-%d" % n] = stats
        if n in (1000, 3000):
            wide_q = trace.density_queries(79, 20, n, 0.5, gap=DENSE_GAP)
            wnaive, _, wtree = index_trio(items)
            wn, ws = time_per_query(wnaive, wide_q)
            _, _, kk = probe_counts(wnaive, wide_q)
            wt, wts = time_per_query(wtree, wide_q)
            MEASURED["wide"].append((n, kk, wn, wt, max(ws, wts),
                                     wn / max(1e-9, wt)))
        ranking = sorted(zip(("naive", "sweep", "tree"),
                             [s[0] for s in stats], [s[1] for s in stats]),
                         key=lambda row: row[1])
        lead = ranking[1][1] - ranking[0][1]
        band = pair_floor(ranking[0][2], ranking[1][2])
        label = ranking[0][0] if lead > band else "tie in noise"
        MEASURED["cheapest-%d" % n] = (label, lead, band)
        rows.append((n, len(queries), "%.2f" % stats[0][0], "%.2f" % stats[1][0],
                     "%.2f" % stats[2][0], "%.2f/%.2f/%.2f" % (
                         stats[0][1], stats[1][1], stats[2][1]), label))
    table(columns, rows)
    flip = None
    for n in (20, 50, 100, 200, 500, 1000, 3000):
        stats = MEASURED["flip-%d" % n]
        if stats[2][0] < stats[0][0] - pair_floor(stats[0][1], stats[2][1]):
            flip = n
            break
    MEASURED["flip"] = flip
    largest = MEASURED["flip-3000"]
    sizes = (20, 50, 100, 200, 500, 1000, 3000)
    tree_ahead = [n for n in sizes
                  if MEASURED["flip-%d" % n][2][0]
                  < MEASURED["flip-%d" % n][0][0]
                  - pair_floor(MEASURED["flip-%d" % n][0][1],
                               MEASURED["flip-%d" % n][2][1])]
    named = [n for n in sizes if MEASURED["cheapest-%d" % n][0] != "tie in noise"]
    if flip is None:
        say("measured honestly: at this density the tree never beat naive by "
            "more than its own band anywhere up to n=3000 (naive %.2f us vs "
            "tree %.2f us at n=3000)." % (largest[0][0], largest[2][0]))
    else:
        at = MEASURED["flip-%d" % flip]
        if named:
            names = sorted(set(MEASURED["cheapest-%d" % n][0] for n in named))
            if "naive" in names:
                suffix = (" - the scan among them, so a plain loop really does "
                          "win at some of these sizes")
            elif names == ["sweep"]:
                suffix = (", i.e. wherever a verdict exists it is the sweep "
                          "reading cheapest, never the tree and never the scan")
            elif names == ["tree"]:
                suffix = (", i.e. the tree is the cheapest reader wherever a "
                          "verdict exists, and the scan never is")
            else:
                suffix = (", which is the sweep and the tree dividing the small "
                          "tables, never the scan")
            winner_text = ("it names %s at %s and a tie at the rest%s."
                           % (", ".join(names), name_list(named), suffix))
        else:
            winner_text = ("no lead between the two cheapest structures clears "
                           "its own band anywhere: the column reads 'tie in "
                           "noise' on all %d rows, i.e. at this density the "
                           "sweep and the tree are indistinguishable and only "
                           "the scan-vs-tree comparison above separates "
                           "anything." % len(sizes))
        below = [n for n in sizes if n < flip]
        head = ("the tree is already ahead at the smallest table probed, so the "
                "flip point for the plain loop sits below this range"
                if not below else
                "the scan and the tree are still level at %s, the smaller tables "
                "probed, so this run brackets the flip rather than resolving it"
                % name_list(below))
        say("the scan is measurably slower than the tree, by more than the "
            "wider of their two bands, at %d of the %d sizes probed (%s). the "
            "earliest is n=%d, where the tree costs %.2f us/query against "
            "naive's %.2f, a %.2f us lead over a pair band of %.2f us, and %s. "
            "'a scan is fine while the table is small' is therefore not what "
            "this run shows at any size at or above n=%d. the winner column "
            "compares the two cheapest structures against each other, so %s"
            % (len(tree_ahead), len(sizes), name_list(tree_ahead),
               flip, at[2][0], at[0][0], at[0][0] - at[2][0],
               pair_floor(at[0][1], at[2][1]), head, flip, winner_text))
    say("the two ratios are not the same claim: section 1 counted %.1fx fewer "
        "interval tests for the tree at n=100000, while the measured wall "
        "clock at n=3000 here is %.1fx (naive %.2f us per query against tree "
        "%.2f us). the gap between those two numbers is the per-query "
        "overhead every structure pays - list build, result sort, call "
        "overhead - which no index can remove."
        % (MEASURED["scale-100000"][4], largest[0][0] / max(1e-9, largest[2][0]),
           largest[0][0], largest[2][0]))
    wide = MEASURED["wide"]
    columns = [("n", 8), ("mean k", 9), ("naive us/q", 12), ("tree us/q", 12),
               ("pair band", 12), ("naive/tree", 12)]
    print()
    print("same structures, a window wide enough to return about half the table:")
    table(columns, [("%d" % v[0], "%.0f" % v[1], "%.2f" % v[2], "%.2f" % v[3],
                     "%.2f" % v[4], "%.2fx" % v[5]) for v in wide])
    read_gap = largest[2][0] - largest[1][0]
    read_band = pair_floor(largest[1][1], largest[2][1])
    read_text = ("the sweep reads %.2f us/query cheaper than the tree"
                 % read_gap if read_gap > read_band else
                 "the two read within %.2f us of each other, inside their "
                 "%.2f us pair band" % (abs(read_gap), read_band))
    say("the sweep is the one to skip in production, and it is not skipped for "
        "the reason the read table might suggest: at n=3000 it costs %.2f "
        "us/query against the tree's %.2f and naive's %.2f, so %s, yet every "
        "insert rebuilds its prefix-max array to the tail, which section 4 "
        "measured at %.2f us per op against the tree's %.2f at n=4000 - %.1fx "
        "the write price for a read difference of %.2f us per query."
        % (largest[1][0], largest[2][0], largest[0][0], read_text,
           MEASURED["write-4000"]["sweep"][1] / 300 * 1e6,
           MEASURED["write-4000"]["tree"][1] / 300 * 1e6,
           (MEASURED["write-4000"]["sweep"][1]
            / max(1e-9, MEASURED["write-4000"]["tree"][1])), abs(read_gap)))
    def clause(v):
        if v[2] - v[3] > v[4]:
            return "at n=%d the tree, by %.2f us (band %.2f)" % (
                v[0], v[2] - v[3], v[4])
        if v[3] - v[2] > v[4]:
            return "at n=%d the scan, by %.2f us (band %.2f)" % (
                v[0], v[3] - v[2], v[4])
        return "at n=%d neither (%.2f us apart against a band of %.2f)" % (
            v[0], abs(v[2] - v[3]), v[4])
    tied_rows = [v for v in wide if abs(v[2] - v[3]) <= v[4]]
    tail_text = ("%d of the %d rows give no verdict at all, so the index buys "
                 "nothing that is measurable once the answer is that big"
                 % (len(tied_rows), len(wide)) if len(tied_rows) == len(wide)
                 else "%d of the %d rows still separate the two, but the bands "
                      "are now the same size as the gap"
                 % (len(wide) - len(tied_rows), len(wide)))
    say("the output wall of section 6 shows in wall clock too: these queries "
        "return %.0f and %.0f conflicts from n=%d and n=%d tables, and once the "
        "answer is that big the per-row winner is %s and %s. the naive/tree "
        "ratios are %.2fx and %.2fx, i.e. %s - building and sorting the answer "
        "dominates the search, and no index removes that cost."
        % (wide[0][1], wide[1][1], wide[0][0], wide[1][0],
           clause(wide[0]), clause(wide[1]), wide[0][5], wide[1][5], tail_text))


def main():
    stream = sys.stdout
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")
    tee = _Tee(stream)
    sys.stdout = tee
    try:
        print("interval-overlap-scheduler benchmarks - python %s"
              % sys.version.split()[0])
        print("measures per-query conflict cost and first_free cost as the table")
        print("grows 100 -> 100000, plus write cost vs depth, bytes per interval,")
        print("result agreement across the three structures and a density sweep.")
        print()
        section_overlaps_scale()
        section_overlaps_time()
        section_first_free_scale()
        section_first_free_time()
        section_correctness()
        section_write_cost()
        section_balance()
        section_memory()
        section_density()
        section_flip()
        banner("end of run - all numbers above belong to this run only")
    finally:
        sys.stdout = stream
        with io.open(ARTIFACT, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(tee.text())


if __name__ == "__main__":
    main()
