"""增广区间树：按 (start, end, label) 有序的平衡 BST，节点带子树最大终点。

树形用 treap（随机优先级）维持：这样“有序输入会不会把树压成一条链”是可以
被实测证伪/证实的属性，而不是一句口头保证。randomize=False 关掉随机化，
得到一棵纯按插入顺序生长的普通 BST，专供基准第 4 节测量退化。
"""

from __future__ import annotations

import random

from .interval import HALF_OPEN, IntervalIndex, overlap, sort_key


class _Node:
    __slots__ = ("item", "prio", "count", "left", "right", "parent", "max_end")

    def __init__(self, item, prio):
        self.item = item
        self.prio = prio
        self.count = 1
        self.left = None
        self.right = None
        self.parent = None
        self.max_end = item[1]


class IntervalTree(IntervalIndex):
    """插入、删除 O(log n)（期望），一次 overlaps 代价约 O(log n + k)。"""

    def __init__(self, convention=HALF_OPEN, randomize=True, seed=19700101):
        super().__init__(convention)
        self.root = None
        self.nodes = 0
        self.randomize = randomize
        self._rng = random.Random(seed)
        self._counter = 0

    # ---- 写路径 -----------------------------------------------------
    def insert(self, interval):
        item = self._check(interval)
        if self.root is None:
            self.root = self._spawn(item)
            self.size += 1
            return item
        node = self.root
        while True:
            self._descend()
            if item == node.item:
                # 完全重复的区间只加计数：节点数、树形、增广值都不动。
                node.count += 1
                self.size += 1
                return item
            if item < node.item:
                if node.left is None:
                    # _attach 自己负责挂接与旋转，这里绝不能再赋值回去：
                    # 旋转后返回的节点已经是 parent 的祖先，赋值会造出环。
                    self._attach(node, item, True)
                    self.size += 1
                    return item
                node = node.left
            else:
                if node.right is None:
                    self._attach(node, item, False)
                    self.size += 1
                    return item
                node = node.right

    def bulk_load(self, intervals):
        for interval in intervals:
            self.insert(interval)
        return self

    def delete(self, interval):
        item = self._check(interval)
        node = self._find(item)
        if node is None:
            return False
        if node.count > 1:
            node.count -= 1
            self.size -= 1
            return True
        self.size -= 1
        self._extract(node)
        return True

    # ---- 读路径 -----------------------------------------------------
    def overlaps(self, query):
        q = self.query(query)
        lo, hi = q[0], q[1]
        strict = self.convention == HALF_OPEN
        out = []
        stack = [] if self.root is None else [self.root]
        visited = 0
        hits = overlap
        conv = self.convention
        while stack:
            node = stack.pop()
            visited += 1
            start, end = node.item[0], node.item[1]
            if start < hi or (not strict and start == hi):
                # 本节点起点仍在窗口内，右子树起点更大，才值得继续走。
                # 这两句只是“必要条件”剪枝，真正的判定仍交给 overlap()，
                # 否则零长度节点会被误判成冲突。
                if hits(node.item, q, conv):
                    out.extend([node.item] * node.count)
                if node.right is not None:
                    stack.append(node.right)
            left = node.left
            if left is not None and (left.max_end > lo or
                                     (not strict and left.max_end == lo)):
                # 增广值说明左子树里还藏着能伸到 lo 之后的区间，必须下去看。
                stack.append(left)
        # 判定次数就是被弹出的节点数，收尾一次结算，避免每节点一次方法调用。
        self._probe(visited)
        out.sort(key=sort_key)
        return out

    def all_intervals(self):
        stack = [] if self.root is None else [self.root]
        seen = set(stack)
        out = []
        while stack:
            node = stack.pop()
            out.extend([node.item] * node.count)
            for child in (node.left, node.right):
                if child is not None and child not in seen:
                    seen.add(child)
                    stack.append(child)
        out.sort(key=sort_key)
        return out

    def height(self):
        """迭代求树高：退化成一串时递归实现会先撞上 Python 的递归上限。"""
        best = 0
        stack = [] if self.root is None else [(self.root, 1)]
        while stack:
            node, depth = stack.pop()
            if depth > best:
                best = depth
            if node.left is not None:
                stack.append((node.left, depth + 1))
            if node.right is not None:
                stack.append((node.right, depth + 1))
        return best

    # ---- 内部维护 ---------------------------------------------------
    def _spawn(self, item):
        if self.randomize:
            prio = self._rng.getrandbits(40)
        else:
            # 关掉随机化后优先级就是插入序号 ⇒ 最小堆退化成“先来者在上”。
            prio = self._counter
            self._counter += 1
        self.nodes += 1
        return _Node(item, prio)

    def _attach(self, parent, item, as_left):
        node = self._spawn(item)
        node.parent = parent
        if as_left:
            parent.left = node
        else:
            parent.right = node
        # 增广值只会因插入变大：一旦某个祖先已经不小于新终点，它上面也必然不小于。
        cursor = parent
        while cursor is not None and cursor.max_end < item[1]:
            cursor.max_end = item[1]
            cursor = cursor.parent
        while node.parent is not None and node.parent.prio > node.prio:
            if node.parent.left is node:
                self._rotate_right(node.parent)
            else:
                self._rotate_left(node.parent)
        return node

    def _pull(self, node):
        hi = node.item[1]
        left = node.left
        if left is not None and left.max_end > hi:
            hi = left.max_end
        right = node.right
        if right is not None and right.max_end > hi:
            hi = right.max_end
        node.max_end = hi

    def _relink(self, old, new):
        parent = old.parent
        new.parent = parent
        if parent is None:
            self.root = new
        elif parent.left is old:
            parent.left = new
        else:
            parent.right = new

    def _rotate_left(self, pivot):
        x = pivot.right
        pivot.right = x.left
        if x.left is not None:
            x.left.parent = pivot
        self._relink(pivot, x)
        x.left = pivot
        pivot.parent = x
        self._pull(pivot)
        self._pull(x)

    def _rotate_right(self, pivot):
        x = pivot.left
        pivot.left = x.right
        if x.right is not None:
            x.right.parent = pivot
        self._relink(pivot, x)
        x.right = pivot
        pivot.parent = x
        self._pull(pivot)
        self._pull(x)

    def _find(self, item):
        node = self.root
        while node is not None:
            self._descend()
            if item == node.item:
                return node
            node = node.left if item < node.item else node.right
        return None

    def _extract(self, node):
        """先把 node 沿堆序旋到只有一子，再摘掉；自下而上重算所有祖先的增广值。"""
        while node.left is not None and node.right is not None:
            if node.left.prio < node.right.prio:
                self._rotate_right(node)
            else:
                self._rotate_left(node)
        child = node.left if node.left is not None else node.right
        parent = node.parent
        if child is not None:
            self._relink(node, child)
        elif parent is None:
            self.root = None
        elif parent.left is node:
            parent.left = None
        else:
            parent.right = None
        self.nodes -= 1
        cursor = parent
        while cursor is not None:
            self._pull(cursor)
            cursor = cursor.parent
