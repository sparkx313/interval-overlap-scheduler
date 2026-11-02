"""排序列表 + 前缀最大终点：一遍扫描能剪枝的中等方案。"""

from __future__ import annotations

from .interval import HALF_OPEN, IntervalIndex, overlap, sort_key

_NEG_INF = float("-inf")


class SweepIndex(IntervalIndex):
    """按 sort_key 排序保存区间，并额外保存“前缀最大终点”。

    查询 [lo, hi) 时：起点 >= hi 的候选直接被二分排除；剩下的前缀从右往左扫，
    一旦 prefmax[j] 说明 0..j 里没有任何区间够到 lo，就立刻停止。
    这条剪枝只在“长区间不扎堆”时有效——一根横跨全表的区间会把 prefmax 顶满，
    退化成和朴素扫描一样的线性代价，基准第 6 节量的正是这件事。
    """

    def __init__(self, convention=HALF_OPEN):
        super().__init__(convention)
        self.keys = []
        self.items = []
        self.prefmax = []

    def insert(self, interval):
        item = self._check(interval)
        key = sort_key(item)
        pos = self._insert_point(key)
        self.keys.insert(pos, key)
        self.items.insert(pos, item)
        self._rebuild_prefix(pos)
        self.size += 1
        return item

    def bulk_load(self, intervals):
        """一次性排序建树：增量 insert 是 O(n)，本表在 10 万行时用它；
        建树方式不改变任何一次查询的判定次数，所以对比仍然公平。
        """
        pairs = sorted((sort_key(i), self._check(i)) for i in intervals)
        self.keys = [p[0] for p in pairs]
        self.items = [p[1] for p in pairs]
        self.prefmax = []
        hi = _NEG_INF
        for item in self.items:
            if item[1] > hi:
                hi = item[1]
            self.prefmax.append(hi)
        self.size = len(self.items)
        return self

    def delete(self, interval):
        item = self._check(interval)
        key = sort_key(item)
        pos = self._lower_bound(key)
        if pos >= len(self.keys) or self.keys[pos] != key:
            return False
        del self.keys[pos]
        del self.items[pos]
        del self.prefmax[pos]
        self._repair_prefix(pos)
        self.size -= 1
        return True

    def overlaps(self, query):
        q = self.query(query)
        lo, hi = q[0], q[1]
        strict = self.convention == HALF_OPEN
        limit = self._candidates_below(hi, strict)
        out = []
        j = limit - 1
        hits = overlap
        conv = self.convention
        while j >= 0:
            top = self.prefmax[j]
            # 前缀里最晚的终点都够不到查询起点，后面所有区间（含 j 自己）都够不到。
            if top < lo or (strict and top == lo):
                break
            item = self.items[j]
            # 剪枝只用“必要条件”，是否真冲突仍由 overlap() 定：
            # 零长度区间（[5,5)）必须落不进任何结果。
            if hits(item, q, conv):
                out.append(item)
            j -= 1
        # 被真正判定过的区间 = 从 limit-1 递减到退出点的前面那些，一次结算。
        self._probe(limit - 1 - j)
        out.sort(key=sort_key)
        return out

    # ---- 内部定位 ---------------------------------------------------
    def _insert_point(self, key):
        """等键新区间插到同键段之后，保证重复区间的插入顺序稳定。"""
        lo, hi = 0, len(self.keys)
        while lo < hi:
            self._descend()
            mid = (lo + hi) // 2
            if self.keys[mid] <= key:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def _lower_bound(self, key):
        lo, hi = 0, len(self.keys)
        while lo < hi:
            self._descend()
            mid = (lo + hi) // 2
            if self.keys[mid] < key:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def _candidates_below(self, value, strict):
        """返回起点的候选上界：半开取第一个 start >= value，闭区间取 start > value。"""
        lo, hi = 0, len(self.keys)
        while lo < hi:
            self._descend()
            mid = (lo + hi) // 2
            start = self.keys[mid][0]
            if start < value or (not strict and start == value):
                lo = mid + 1
            else:
                hi = mid
        return lo

    def _rebuild_prefix(self, pos):
        """插入后：prefmax 与 items 长度重新对齐，并把 pos 起的整条后缀重算一遍。"""
        prev = self.prefmax[pos - 1] if pos > 0 else _NEG_INF
        hi = prev if prev > self.items[pos][1] else self.items[pos][1]
        self.prefmax.insert(pos, hi)
        i = pos + 1
        n = len(self.items)
        while i < n:
            end = self.items[i][1]
            if end > hi:
                hi = end
            self.prefmax[i] = hi
            i += 1

    def _repair_prefix(self, pos):
        """删除后：三个列表已同步收缩，只需从 pos 起重算到表尾。"""
        if pos >= len(self.items):
            return
        hi = self.prefmax[pos - 1] if pos > 0 else _NEG_INF
        i = pos
        n = len(self.items)
        while i < n:
            end = self.items[i][1]
            if end > hi:
                hi = end
            self.prefmax[i] = hi
            i += 1
