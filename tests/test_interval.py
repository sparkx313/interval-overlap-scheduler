"""三套结构的语义测试：任何一条失败都意味着边界约定被写坏了。"""

from __future__ import annotations

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from intervallib import (  # noqa: E402
    HALF_OPEN,
    INCLUSIVE,
    IntervalError,
    IntervalTree,
    NaiveIndex,
    SweepIndex,
    convention_for,
    make,
    overlap,
)
from intervallib import trace  # noqa: E402

STRUCTURES = ("naive", "sweep", "tree")


def build(kind, convention=HALF_OPEN, **kwargs):
    if kind == "naive":
        return NaiveIndex(convention)
    if kind == "sweep":
        return SweepIndex(convention)
    if kind == "tree":
        return IntervalTree(convention, **kwargs)
    raise AssertionError(kind)


class TestIntervalContract(unittest.TestCase):
    """make / overlap / convention_for 自身的规则。"""

    def test_inverted_bounds_rejected(self):
        with self.assertRaises(IntervalError):
            make(10, 5)

    def test_inverted_is_value_error(self):
        self.assertTrue(issubclass(IntervalError, ValueError))

    def test_zero_length_allowed(self):
        self.assertEqual(make(7, 7), (7.0, 7.0, ""))

    def test_nan_endpoint_rejected(self):
        with self.assertRaises(IntervalError):
            make(float("nan"), 5)

    def test_non_numeric_endpoint_rejected(self):
        with self.assertRaises(IntervalError):
            make("3", 9)

    def test_bool_endpoint_rejected(self):
        with self.assertRaises(IntervalError):
            make(True, 5)

    def test_label_must_be_string(self):
        with self.assertRaises(IntervalError):
            make(0, 1, 7)

    def test_convention_for_mapping(self):
        self.assertEqual(convention_for(True), HALF_OPEN)
        self.assertEqual(convention_for(False), INCLUSIVE)

    def test_unknown_convention_rejected(self):
        with self.assertRaises(IntervalError):
            build("tree", "open-closed")

    def test_overlap_half_open_touching(self):
        self.assertFalse(overlap(make(0, 10), make(10, 20), HALF_OPEN))

    def test_overlap_inclusive_touching(self):
        self.assertTrue(overlap(make(0, 10), make(10, 20), INCLUSIVE))

    def test_overlap_zero_length_half_open(self):
        self.assertFalse(overlap(make(5, 15), make(10, 10), HALF_OPEN))

    def test_overlap_zero_length_inclusive(self):
        self.assertTrue(overlap(make(5, 15), make(10, 10), INCLUSIVE))

    def test_endpoint_order_does_not_matter(self):
        self.assertEqual(overlap(make(0, 10), make(9, 30), HALF_OPEN),
                         overlap(make(9, 30), make(0, 10), HALF_OPEN))


class SemanticMixin:
    """每种结构都要通过的共同语义；KIND 决定测哪一个。

    故意不继承 TestCase：只有下面三个具体子类会被 unittest 收集，
    于是同一套断言在 naive / sweep / tree 上各跑一遍。
    """

    KIND = None

    def idx(self, convention=HALF_OPEN, **kwargs):
        return build(self.KIND, convention, **kwargs)

    def loaded(self, items, convention=HALF_OPEN):
        index = build(self.KIND, convention)
        for item in items:
            index.insert(item)
        return index

    def test_touching_is_not_overlap_by_default(self):
        index = self.loaded([make(0, 10, "a")])
        self.assertEqual(index.overlaps(make(10, 20, "q")), [])

    def test_touching_is_overlap_when_back_to_back_forbidden(self):
        index = self.loaded([make(0, 10, "a")], INCLUSIVE)
        self.assertEqual(index.overlaps(make(10, 20, "q")),
                         [make(0, 10, "a")])

    def test_zero_length_query_matches_nothing_half_open(self):
        index = self.loaded([make(0, 10, "a"), make(10, 20, "b")])
        self.assertEqual(index.overlaps(make(10, 10, "q")), [])

    def test_zero_length_query_matches_containing_inclusive(self):
        index = self.loaded([make(0, 10, "a"), make(10, 20, "b")], INCLUSIVE)
        self.assertEqual(index.overlaps(make(10, 10, "q")),
                         [make(0, 10, "a"), make(10, 20, "b")])

    def test_nested_intervals_both_found(self):
        index = self.loaded([make(0, 100, "long"), make(20, 30, "small")])
        self.assertEqual(index.overlaps(make(25, 26, "q")),
                         [make(0, 100, "long"), make(20, 30, "small")])

    def test_disjoint_query_returns_empty(self):
        index = self.loaded(trace.boundary_cases())
        self.assertEqual(index.overlaps(make(10_000, 10_001, "q")), [])

    def test_size_counts_duplicates(self):
        index = self.loaded([make(0, 10, "a"), make(0, 10, "a")])
        self.assertEqual(len(index), 2)

    def test_duplicate_intervals_appear_twice_in_result(self):
        index = self.loaded([make(0, 10, "a"), make(0, 10, "a")])
        self.assertEqual(index.overlaps(make(1, 2, "q")),
                         [make(0, 10, "a"), make(0, 10, "a")])

    def test_delete_removes_one_copy(self):
        index = self.loaded([make(0, 10, "a"), make(0, 10, "a")])
        self.assertTrue(index.delete(make(0, 10, "a")))
        self.assertEqual(len(index), 1)
        self.assertEqual(len(index.overlaps(make(1, 2, "q"))), 1)

    def test_delete_missing_returns_false(self):
        index = self.loaded([make(0, 10, "a")])
        self.assertFalse(index.delete(make(50, 60, "ghost")))
        self.assertFalse(index.delete(make(0, 10, "other-label")))
        self.assertEqual(len(index), 1)

    def test_delete_everything(self):
        items = trace.boundary_cases()
        index = self.loaded(items)
        for item in items:
            self.assertTrue(index.delete(item))
        self.assertEqual(len(index), 0)
        self.assertEqual(index.overlaps(make(0, 1000, "q")), [])

    def test_delete_on_empty_index(self):
        self.assertFalse(self.idx().delete(make(0, 1, "x")))

    def test_inverted_insert_rejected(self):
        with self.assertRaises(IntervalError):
            self.idx().insert((5, 1, "bad"))

    def test_count_conflicts_matches_overlaps(self):
        index = self.loaded(trace.boundary_cases())
        for query in trace.boundary_queries():
            self.assertEqual(index.count_conflicts(query),
                             len(index.overlaps(query)))

    def test_back_to_back_option_changes_schedule(self):
        items = [make(9, 10, "standup")]
        loose = self.loaded(items, HALF_OPEN)
        strict = self.loaded(items, INCLUSIVE)
        self.assertEqual(loose.overlaps(make(10, 11, "sync")), [])
        self.assertEqual(strict.overlaps(make(10, 11, "sync")), items)

    def test_first_free_before_first_booking(self):
        index = self.loaded([make(10, 20, "a")])
        self.assertEqual(index.first_free(0, 5, 100), 0.0)

    def test_first_free_jumps_to_end_of_conflict(self):
        index = self.loaded([make(0, 10, "a"), make(15, 25, "b")])
        self.assertEqual(index.first_free(0, 5, 100), 10.0)

    def test_first_free_chains_over_several_bookings(self):
        index = self.loaded([make(0, 10, "a"), make(15, 25, "b")])
        self.assertEqual(index.first_free(0, 8, 100), 25.0)

    def test_first_free_respects_horizon_edge(self):
        index = self.loaded([make(0, 95, "a")])
        self.assertEqual(index.first_free(0, 5, 100), 95.0)

    def test_first_free_returns_none_at_horizon(self):
        index = self.loaded([make(0, 95, "a")])
        self.assertIsNone(index.first_free(0, 6, 100))

    def test_first_free_horizon_before_request(self):
        index = self.idx()
        self.assertIsNone(index.first_free(50, 60, 55))

    def test_first_free_zero_duration_returns_start(self):
        index = self.loaded([make(0, 10, "a")])
        self.assertEqual(index.first_free(0, 0, 100), 0.0)

    def test_first_free_back_to_back_forbidden_gives_none(self):
        # 闭区间约定下 [10, x) 与 (0,10] 端点重合即冲突，而连续时间里
        # “刚好大于 10”的最小点不存在：这里必须诚实地返回 None。
        index = self.loaded([make(0, 10, "a")], INCLUSIVE)
        self.assertIsNone(index.first_free(10, 15, 100))

    def test_first_free_back_to_back_allowed(self):
        index = self.loaded([make(0, 10, "a")], HALF_OPEN)
        self.assertEqual(index.first_free(10, 15, 100), 10.0)

    def test_negative_time_axis(self):
        index = self.loaded([make(-30, -20, "a")])
        self.assertEqual(index.overlaps(make(-25, -24, "q")),
                         [make(-30, -20, "a")])

    def test_query_without_label_accepted(self):
        index = self.loaded([make(0, 10, "a")])
        self.assertEqual(len(index.overlaps((5, 6))), 1)

    def test_random_table_agrees_with_naive_scan(self):
        """同一种子喂给参照实现与本结构：结果必须逐条相同。"""
        ref = NaiveIndex(HALF_OPEN)
        dut = self.idx()
        items = trace.build_table(4242, 400)
        for item in items:
            ref.insert(item)
            dut.insert(item)
        for query in trace.build_queries(991, 60, 400):
            self.assertEqual(ref.overlaps(query), dut.overlaps(query))

    def test_random_table_agrees_inclusive(self):
        ref = NaiveIndex(INCLUSIVE)
        dut = self.idx(INCLUSIVE)
        items = trace.build_table(777, 300)
        for item in items:
            ref.insert(item)
            dut.insert(item)
        for query in trace.build_queries(77, 60, 300):
            self.assertEqual(ref.overlaps(query), dut.overlaps(query))

    def test_boundary_collection_agrees(self):
        ref = NaiveIndex(HALF_OPEN)
        dut = self.idx()
        for item in trace.boundary_cases():
            ref.insert(item)
            dut.insert(item)
        for query in trace.boundary_queries():
            self.assertEqual(ref.overlaps(query), dut.overlaps(query))

    def test_delete_then_query_agrees_with_naive(self):
        rng = random.Random(2024)
        items = trace.build_table(31337, 200)
        ref = NaiveIndex(HALF_OPEN)
        dut = self.idx()
        for item in items:
            ref.insert(item)
            dut.insert(item)
        for _ in range(60):
            victim = items[rng.randrange(len(items))]
            self.assertEqual(ref.delete(victim), dut.delete(victim))
        for query in trace.build_queries(5, 40, 200):
            self.assertEqual(ref.overlaps(query), dut.overlaps(query))

    def test_first_free_agrees_with_naive(self):
        ref = NaiveIndex(HALF_OPEN)
        dut = self.idx()
        for item in trace.build_table(20250107, 250):
            ref.insert(item)
            dut.insert(item)
        for start, end, horizon in trace.first_free_cases(3, 40, 250):
            self.assertEqual(ref.first_free(start, end, horizon),
                             dut.first_free(start, end, horizon))

    def test_full_span_query_probes_every_interval(self):
        # 覆盖全表的查询不存在任何可剪枝的候选，三套结构都必须判定 50 次。
        index = self.loaded(trace.build_table(11, 50))
        index.reset_stats()
        index.overlaps(make(0, trace.span_of(50), "q"))
        probes, _ = index.stats()
        self.assertEqual(probes, 50)


class TestNaiveSemantics(SemanticMixin, unittest.TestCase):
    KIND = "naive"


class TestSweepSemantics(SemanticMixin, unittest.TestCase):
    KIND = "sweep"


class TestTreeSemantics(SemanticMixin, unittest.TestCase):
    KIND = "tree"

    def test_duplicate_collapse_into_one_node(self):
        index = IntervalTree()
        for _ in range(3):
            index.insert(make(0, 10, "a"))
        self.assertEqual(index.nodes, 1)
        self.assertEqual(len(index), 3)
        index.delete(make(0, 10, "a"))
        self.assertEqual(index.nodes, 1)

    def test_height_stays_logarithmic_on_sorted_input(self):
        index = IntervalTree()
        for item in trace.sorted_table(4000):
            index.insert(item)
        self.assertLess(index.height(), 120)
        self.assertGreater(index.height(), 10)

    def test_no_randomization_degenerates_on_sorted_input(self):
        chain = IntervalTree(randomize=False)
        for item in trace.sorted_table(2000):
            chain.insert(item)
        self.assertEqual(chain.height(), 2000)
        tail = make(trace.span_of(2000) - 1, trace.span_of(2000), "tail")
        chain.reset_stats()
        self.assertEqual(chain.overlaps(tail), [])
        self.assertEqual(chain.comparisons, 2000)
        balanced = IntervalTree().bulk_load(trace.sorted_table(2000))
        balanced.reset_stats()
        self.assertEqual(balanced.overlaps(tail), [])
        self.assertLess(balanced.comparisons, 200)

    def test_augmentation_stays_correct_under_churn(self):
        index = IntervalTree()
        ref = NaiveIndex(HALF_OPEN)
        items = trace.build_table(90210, 300)
        rng = random.Random(1)
        live = []
        for item in items[:150]:
            index.insert(item)
            ref.insert(item)
            live.append(item)
        for _ in range(200):
            if live and rng.random() < 0.45:
                victim = live.pop(rng.randrange(len(live)))
                self.assertTrue(index.delete(victim))
                ref.delete(victim)
            else:
                fresh = items[rng.randrange(len(items))]
                index.insert(fresh)
                ref.insert(fresh)
                live.append(fresh)
            self._check_max_end(index)
        for query in trace.build_queries(3, 30, 300):
            self.assertEqual(ref.overlaps(query), index.overlaps(query))

    def test_insert_and_delete_keep_sorted_order(self):
        index = IntervalTree()
        for item in trace.build_table(5, 120):
            index.insert(item)
        flat = index.all_intervals()
        self.assertEqual(flat, sorted(flat, key=lambda i: (i[0], i[1], i[2])))

    def test_bulk_load_equals_incremental(self):
        items = trace.build_table(6060, 500)
        bulk = IntervalTree().bulk_load(items)
        step = IntervalTree()
        for item in items:
            step.insert(item)
        for query in trace.build_queries(9, 30, 500):
            self.assertEqual(bulk.overlaps(query), step.overlaps(query))

    def _check_max_end(self, index):
        """增广不变式：max_end 必须等于子树里最晚的终点，否则查询会漏。"""
        stack = [] if index.root is None else [index.root]
        while stack:
            node = stack.pop()
            best = node.item[1]
            for child in (node.left, node.right):
                if child is not None:
                    best = max(best, child.max_end)
                    stack.append(child)
            self.assertEqual(node.max_end, best)


class TestSweepInvariants(unittest.TestCase):
    def test_prefmax_invariant_after_inserts(self):
        index = SweepIndex()
        for item in trace.build_table(404, 200):
            index.insert(item)
            index.insert(make(5, 5, "zero"))
        running = float("-inf")
        for i, item in enumerate(index.items):
            running = max(running, item[1])
            self.assertEqual(index.prefmax[i], running)

    def test_keys_stay_sorted(self):
        index = SweepIndex()
        for item in trace.build_table(77, 180):
            index.insert(item)
        self.assertEqual(index.keys, sorted(index.keys))

    def test_incremental_equals_bulk_load(self):
        items = trace.build_table(8080, 250)
        bulk = SweepIndex().bulk_load(items)
        step = SweepIndex()
        for item in items:
            step.insert(item)
        self.assertEqual(bulk.items, step.items)
        self.assertEqual(bulk.prefmax, step.prefmax)

    def test_delete_repairs_prefix(self):
        items = trace.build_table(1212, 200)
        index = SweepIndex().bulk_load(items)
        for victim in items[:120]:
            self.assertTrue(index.delete(victim))
        running = float("-inf")
        for i, item in enumerate(index.items):
            running = max(running, item[1])
            self.assertEqual(index.prefmax[i], running)
        ref = NaiveIndex(HALF_OPEN).bulk_load(items[120:])
        for query in trace.build_queries(2, 30, 200):
            self.assertEqual(ref.overlaps(query), index.overlaps(query))

    def test_prune_still_finds_long_interval(self):
        index = SweepIndex().bulk_load([make(0, 10_000, "long"),
                                        make(500, 600, "mid")])
        self.assertEqual(index.overlaps(make(9_000, 9_100, "q")),
                         [make(0, 10_000, "long")])

    def test_empty_index_queries(self):
        index = SweepIndex()
        self.assertEqual(index.overlaps(make(0, 1, "q")), [])
        self.assertEqual(index.first_free(0, 1, 10), 0.0)


class TestCrossStructureEquality(unittest.TestCase):
    """三套结构必须逐条一致；这是本库唯一的“正确性”定义。"""

    def compare(self, items, queries, convention):
        tables = []
        for kind in STRUCTURES:
            index = build(kind, convention)
            index.bulk_load(items)
            tables.append(index)
        disagreements = 0
        for query in queries:
            results = [index.overlaps(query) for index in tables]
            if not all(res == results[0] for res in results[1:]):
                disagreements += 1
        return disagreements, len(queries), [len(r) for r in results]

    def test_random_queries_agree_half_open(self):
        items = trace.build_table(630, 900)
        queries = trace.build_queries(631, 150, 900)
        bad, total, _ = self.compare(items, queries, HALF_OPEN)
        self.assertEqual(bad, 0)
        self.assertEqual(total, 150)

    def test_random_queries_agree_inclusive(self):
        items = trace.build_table(630, 900)
        queries = trace.build_queries(631, 150, 900)
        bad, _, _ = self.compare(items, queries, INCLUSIVE)
        self.assertEqual(bad, 0)

    def test_boundary_queries_agree(self):
        bad, _, sizes = self.compare(trace.boundary_cases(),
                                     trace.boundary_queries(), HALF_OPEN)
        self.assertEqual(bad, 0)
        # 最后一问覆盖全域：9 条里两条零长度区间按半开约定不算冲突，故为 7。
        self.assertEqual(sizes[0], 7)

    def test_zero_and_touching_only_agree(self):
        items = [make(0, 10, "a"), make(10, 10, "z"), make(10, 20, "b"),
                 make(20, 30, "c")]
        queries = [make(10, 10, "q1"), make(10, 11, "q2"), make(9, 10, "q3"),
                   make(0, 30, "q4"), make(30, 30, "q5")]
        for convention in (HALF_OPEN, INCLUSIVE):
            bad, _, _ = self.compare(items, queries, convention)
            self.assertEqual(bad, 0)

    def test_count_conflicts_agree(self):
        items = trace.build_table(21, 600)
        queries = trace.build_queries(22, 80, 600)
        tables = [build(kind, HALF_OPEN).bulk_load(items) for kind in STRUCTURES]
        for query in queries:
            counts = [index.count_conflicts(query) for index in tables]
            self.assertEqual(len(set(counts)), 1)

    def test_first_free_agree(self):
        items = trace.build_table(88, 700)
        tables = [build(kind, HALF_OPEN).bulk_load(items) for kind in STRUCTURES]
        cases = trace.first_free_cases(89, 60, 700)
        for start, end, horizon in cases:
            answers = [index.first_free(start, end, horizon) for index in tables]
            self.assertEqual(len(set(answers)), 1)
            self.assertTrue(all(a is None or a >= start for a in answers))

    def test_dense_table_agree(self):
        items = trace.build_table(5, 500, gap=150.0, length=120.0)
        queries = trace.build_queries(6, 40, 500, gap=150.0)
        bad, _, _ = self.compare(items, queries, HALF_OPEN)
        self.assertEqual(bad, 0)

    def test_back_to_back_switch_matches_convention_switch(self):
        items = [make(9, 10, "a")]
        for kind in STRUCTURES:
            loose = build(kind, convention_for(True)).bulk_load(items)
            strict = build(kind, convention_for(False)).bulk_load(items)
            self.assertEqual(loose.overlaps(make(10, 11, "q")), [])
            self.assertEqual(strict.overlaps(make(10, 11, "q")), items)


if __name__ == "__main__":
    unittest.main()
