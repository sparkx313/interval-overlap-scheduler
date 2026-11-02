"""区间表示、边界约定与三种结构的公共接口。

本模块只关心两件事：区间长什么样，以及“重叠”按哪种约定判定。
naive / sweep / tree 三个实现共享这里的语义，因此它们的查询结果必须逐条相同。
"""

from __future__ import annotations

import math

HALF_OPEN = "half-open"
INCLUSIVE = "inclusive"

_CONVENTIONS = (HALF_OPEN, INCLUSIVE)


class IntervalError(ValueError):
    """非法区间。make() 与所有 insert() 只抛这一种异常。"""


def make(start, end, label=""):
    """构造区间 (start, end, label)。

    规则：拒绝倒置区间（end < start），但允许零长度（end == start）——
    半开约定下它不覆盖任何时刻，这是有意义的空档，不是错误。
    标签必须是字符串：树按 (start, end, label) 做三元组比较，混类型会让比较失去全序。
    """
    if not _is_number(start) or not _is_number(end):
        raise IntervalError("端点必须是数字：%r, %r" % (start, end))
    if start > end:
        raise IntervalError("倒置区间：start=%r > end=%r" % (start, end))
    if not isinstance(label, str):
        raise IntervalError("标签必须是字符串，收到 %r" % (label,))
    return (float(start), float(end), label)


def _is_number(value):
    # NaN 的比较永远为假，会静默穿过校验并污染增广最大值，所以在这里就挡掉。
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and not (isinstance(value, float) and math.isnan(value))


def convention_for(allow_back_to_back):
    """把调度器口语“能不能背靠背”翻译成边界约定。

    allow_back_to_back=True  ⇔ 半开 [a, b)：[5,7) 与 [7,9) 不冲突。
    allow_back_to_back=False ⇔ 闭区间语义：端点重合即冲突。
    """
    return HALF_OPEN if allow_back_to_back else INCLUSIVE


def overlap(a, b, convention):
    """判定两个区间是否冲突；这是全库唯一一处边界约定的实现。

    写成 max/min 形式而不是常见的 `a.lo < b.hi and b.lo < a.hi`：
    后者的等价性依赖“两个区间都非空”，一旦查询是零长度区间（[5,5)）就会把
    空集判成冲突。排班里“查某一分钟有没有空”正是这种查询，所以不能省。
    """
    lo = a[0] if a[0] > b[0] else b[0]
    hi = a[1] if a[1] < b[1] else b[1]
    if convention == HALF_OPEN:
        return lo < hi
    return lo <= hi


def sort_key(interval):
    """结果排序键：数字端点优先，标签用字符串形式兜底，保证输出顺序可复现。"""
    return (interval[0], interval[1], str(interval[2]))


class IntervalIndex:
    """所有索引的公共接口：insert / delete / overlaps / first_free / count_conflicts。

    计数器语义（基准脚本依赖它，所以定义得很死）：
      comparisons —— 被拿出来和查询区间做过判定的“已存区间”个数（探针数）；
      nav         —— 结构内部为了定位而做的端点比较（二分步数、树下降层数）。
    对结果排序所花的比较不计入任何一侧。
    """

    def __init__(self, convention=HALF_OPEN):
        if convention not in _CONVENTIONS:
            raise IntervalError("未知边界约定：%r" % (convention,))
        self.convention = convention
        self.size = 0
        self.comparisons = 0
        self.nav = 0
        self.jumps = 0

    # ---- 子类必须实现的三个原语 -------------------------------------
    def insert(self, interval):
        raise NotImplementedError

    def delete(self, interval):
        raise NotImplementedError

    def overlaps(self, query):
        raise NotImplementedError

    # ---- 公共派生行为 ------------------------------------------------
    def count_conflicts(self, query):
        # 刻意复用 overlaps()：两条代码路径一旦分开写就会漂移，
        # 而“计数”和“列表”对不上正是排班系统里最难查的那类 bug。
        return len(self.overlaps(query))

    def query(self, interval):
        """把用户给的 2 元或 3 元组规范化成合法查询区间。"""
        if (len(interval) == 3 and type(interval[0]) is float
                and type(interval[1]) is float and type(interval[2]) is str
                and interval[0] <= interval[1]):
            # 快路径：make() 产出过的合法三元组不必再校验一遍，否则每次查询都
            # 要重建元组，常数开销会淹没结构之间的差异（基准 6b 可见）。
            return interval
        if len(interval) == 2:
            return make(interval[0], interval[1])
        return make(interval[0], interval[1], interval[2])

    def first_free(self, start, end, horizon):
        """找最早能放下 [start, end) 这段时长的起点，找不到返回 None。

        duration = end - start 是必须保住的时长；horizon 是最晚结束时刻。
        候选起点只需考虑 start 本身和已存区间的终点：可行集对“向左贴着某个终点”
        封闭，所以不必扫描全体实数。
        闭区间约定下的后果：若最早候选恰好被某个“终点正落在它上面”的区间封住，
        连续时间里不存在最小可用起点，本实现返回 None，而不是伪造 start + epsilon。
        """
        request = make(start, end)
        duration = request[1] - request[0]
        if request[0] + duration > horizon:
            return None
        cursor = request[0]
        while cursor + duration <= horizon:
            hits = self.overlaps((cursor, cursor + duration, ""))
            self.jumps += 1
            if not hits:
                return cursor
            nxt = max(item[1] for item in hits)
            if nxt <= cursor:
                return None
            cursor = nxt
        return None

    def reset_stats(self):
        self.comparisons = 0
        self.nav = 0

    def stats(self):
        return (self.comparisons, self.nav)

    def __len__(self):
        return self.size

    def _probe(self, count=1):
        self.comparisons += count

    def _descend(self, count=1):
        self.nav += count

    def _check(self, interval):
        """insert 入口的统一校验：拒绝倒置，接受 make() 产出的三元组。"""
        if len(interval) == 2:
            return make(interval[0], interval[1])
        return make(interval[0], interval[1], interval[2])

    def _hits(self, a, b):
        return overlap(a, b, self.convention)
