"""朴素全表扫描：所有正确性结论的参照实现。"""

from __future__ import annotations

from .interval import IntervalIndex, overlap, sort_key


class NaiveIndex(IntervalIndex):
    """把区间原样存进一个 list，查询时逐条判定。

    它没有任何索引技巧，所以永远“对”；代价是每次查询恰好 n 次判定。
    真实项目里排班冲突检测就是这么写的，在 200 行时看不出问题，10 万行时才发现。
    """

    def __init__(self, convention="half-open"):
        super().__init__(convention)
        self.items = []

    def insert(self, interval):
        item = self._check(interval)
        self.items.append(item)
        self.size += 1
        return item

    def bulk_load(self, intervals):
        for interval in intervals:
            self.insert(interval)
        return self

    def delete(self, interval):
        item = self._check(interval)
        try:
            # list.remove 在 C 层比较整个元组，比 Python 循环快一个量级；
            # 因此删除不计入 comparisons（基准只统计查询探针）。
            self.items.remove(item)
        except ValueError:
            return False
        self.size -= 1
        return True

    def overlaps(self, query):
        q = self.query(query)
        hits = overlap
        conv = self.convention
        # 扫描用推导式（就是生产代码里那种写法），计数一次性结算：
        # 否则每根区间的计数器调用会让 naive 慢得冤枉，比较也就不公平了。
        out = [item for item in self.items if hits(item, q, conv)]
        self._probe(len(self.items))
        out.sort(key=sort_key)
        return out
