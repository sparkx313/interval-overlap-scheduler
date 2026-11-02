# interval-overlap-scheduler

Conflict detection for bookings usually ships as "loop over every appointment and
compare", which costs one stored-interval test per row at any table size. This
repo implements that scan, a sorted-list sweep and an augmented interval tree
behind one interface and measures all three. Four findings from the single
authoritative run kept in `benchmarks/last_run.txt` (python 3.11.9):

- Per `overlaps` query the scan tests exactly `n` rows (100.0 at n=100, 100000.0
  at n=100000) while the tree needs 7.7 -> 21.1, so its edge grows from 12.96x at
  n=100 to 4733.73x at n=100000.
- The scan never won a timed row. At the smallest table probed in 6b (n=20) the
  tree costs 2.61 us/query against the scan's 4.47 - a 1.86 us lead over a
  pass-to-pass band of 0.86 us - and the tree is clear of the scan at 7 of the 7
  sizes, so "a loop is fine while the table is small" is not what this run shows.
- The two indexed structures are a statistical tie on reads. The sweep tests
  fewer rows than the tree at every size in section 1 (0.6 against 21.1 probes at
  n=100000), yet on the clock it is named cheaper at one of the three timed sizes
  and by at most 1.8 us/query; what separates them is writing, where the sweep
  costs 394.67 us per round trip against the tree's 5.42 at n=4000.
- `first_free`, the query an index handles worst, still pays: 1.99 -> 2.20
  searches per call over a 1000x growth, 4583.33x under the scan at n=100000, and
  9.6 us per attempt at n=50000.

Every verdict in `last_run.txt` is pairwise against the wider of the two
competing structures' own pass-to-pass bands, never against one global floor: a
shared floor would have to be the scan's band, which at n=100000 is 8069.3 us,
i.e. thousands of times larger than the difference it would be asked to referee.
The tables print those bands per structure, so each claim can be checked against
the row it comes from.

## What it does / does not do

- One interface over three structures - `insert`, `delete`, `overlaps`,
  `count_conflicts`, `first_free` - so swapping the class changes no call site and
  a measured difference can only come from the algorithm.
- Two boundary conventions picked at build time: half-open `[a, b)`
  (`allow_back_to_back=True`, what schedulers mean) and inclusive `[a, b]`.
- Multiset semantics: the same `(start, end, label)` inserted twice is two rows
  and both come back; zero-length rows are legal and conflict with nothing.
- Instrumentation: stored rows actually tested (`comparisons`), locating
  comparisons (`nav`), searches per `first_free` call (`jumps`).
- Not a calendar: no time zones, DST, recurrence, priority, persistence, async or
  thread safety. Endpoints are plain floats on whatever axis you pick.
- No self-tuning: the tree stays balanced only because priorities are randomized;
  `randomize=False` exists so degeneration can be measured, not for production.

## Install

```bash
git clone <this repo url> interval-overlap-scheduler
cd interval-overlap-scheduler
python -u -m unittest discover -s tests -p 'test_*.py' -t tests
```

Python 3.9+, standard library only (`dependencies = []` in `pyproject.toml`).

## Quick start

Every comment below is the real output of that line in this run.

```python
from intervallib import IntervalTree, NaiveIndex, SweepIndex, convention_for, make

booked = [make(9, 10, "standup"), make(11, 12, "review")]
tree = IntervalTree(convention_for(allow_back_to_back=True))
for item in booked:
    tree.insert(item)

tree.overlaps(make(9, 11, "ask"))
# -> [(9.0, 10.0, 'standup')]
tree.count_conflicts(make(9, 11, "ask"))
# -> 1
tree.overlaps(make(10, 11, "next-hour"))
# -> []        half-open: 10:00 is where standup ends, so the hour is free
tree.first_free(9, 10, 20)
# -> 10.0      earliest start that fits a one-hour block
tree.first_free(10, 11, 20)
# -> 10.0      the requested window is already free

strict = IntervalTree(convention_for(allow_back_to_back=False))
for item in booked:
    strict.insert(item)
strict.overlaps(make(10, 11, "next-hour"))
# -> [(9.0, 10.0, 'standup'), (11.0, 12.0, 'review')]
strict.first_free(10, 11, 20)
# -> None      every candidate start sits on a stored end; no earliest point
#              exists in the continuum, so it refuses instead of inventing one

SweepIndex().first_free(9, 10, 20)
# -> 9.0       empty table: the requested start is free
len(NaiveIndex().overlaps(make(0, 1, "empty")))
# -> 0
tree.delete(make(9, 10, "standup")), tree.count_conflicts(make(9, 11, "ask"))
# -> (True, 0) delete removes exactly one row
try:
    tree.insert(make(10, 9, "inverted"))
except Exception as exc:
    print(type(exc).__name__)
# -> IntervalError
```

## Measured results

Each block is copied line-for-line from `benchmarks/last_run.txt`, column headers
included; the prose under it claims only what those lines show. `*/cq` counts the
stored intervals a query actually tested, `*/us` is best-of-5 wall clock, and
`band n/s/t` gives the naive, sweep and tree pass-to-pass bands for that row in
us - the number any tie call has to clear.

### 1 and 1b. `overlaps`: probes, then wall clock

```text
       n     q  mean_k  naive/cq  sweep/cq   tree/cq  sweep_nav naive/tree
------- ----- ------- --------- --------- --------- ---------- ----------
     100   200    0.59     100.0       0.6       7.7        6.8     12.96x
    1000   200    0.56    1000.0       0.6      11.6       10.0     86.51x
    5000   200    0.52    5000.0       0.5      15.1       12.4    330.58x
   20000    40    0.53   20000.0       0.5      17.6       14.3   1136.36x
  100000    40    0.62  100000.0       0.6      21.1       16.7   4733.73x
```

```text
      n q/pass naive us/q sweep us/q  tree us/q      band n/s/t naive/tree
------ ------ ---------- ---------- ---------- --------------- ----------
   1000    250      157.0        3.0        3.4    13.0/0.2/2.1      46.4x
  10000     25     1921.4        3.9        4.9   559.2/0.2/1.1     393.1x
 100000     20    18329.3        4.5        6.2  8069.3/0.4/0.1    2942.1x
```

The scan column is the table size by construction (100.0 -> 100000.0) while
`mean_k` barely moves (0.59 -> 0.62), so the tree's 7.7 -> 21.1 tracks rows, not
output; the sweep's own endpoints are 0.59 -> 0.62, which the one-decimal column
rounds to a flat 0.6 -> 0.6. In wall clock the n=100000 gap clears its 8069.3 us
band by 2x while the n=1000 gap clears its much smaller 13.0 us band by 11.8x -
both are real, and the larger table is not automatically the stronger evidence:
this run's noise decided that. Between the two indexed structures the sweep is
named cheaper only at n=100000, by 1.8 us/query, and is a tie at n=1000 and
n=10000; the scan gap at that same size is 18323.1 us. Probe counts say the sweep
wins outright, the clock says it wins almost nothing.

### 2 and 2b. `first_free`: probes, then wall clock

```text
       n  cases hops/call naive/call sweep/call tree/call naive/tree
------- ------ --------- ---------- ---------- --------- ----------
     100    120      1.99      199.2        1.4      15.1     13.20x
    1000    120      2.01     2008.3        1.5      23.0     87.19x
    5000    120      1.90     9500.0        1.2      29.1    326.93x
   20000     20      1.85    37000.0        0.9      31.4   1178.34x
  100000     20      2.20   220000.0        1.4      48.0   4583.33x
```

```text
       n cases/pass   naive us   sweep us    tree us     band n/s/t naive/tree
------- ---------- ---------- ---------- ---------- -------------- ----------
    1000         60      324.8        6.7        7.9   27.0/0.4/1.3      40.9x
   10000         20     2826.5        7.2        8.7  202.8/0.3/0.3     323.0x
   50000         20    16566.0        7.4        9.6 1733.8/0.5/1.0    1725.6x
```

A booking attempt is not one search but 1.99 -> 2.20 of them, each hop a fresh
descent, and all requests stayed satisfiable. The tree's lead narrows to 4583.33x
against 4733.73x for a single `overlaps` query on the same table: the hop loop
spends part of the index's advantage. Wall clock says the same thing at n=1000
(46.4x for `overlaps` against 40.9x for `first_free`, so 11.8% of the measured
lead goes), and this run cannot separate the extra search from the per-call
overhead each hop drags along. On counts the sweep is the cheapest of the three
here too (1.4 tests per call against the tree's 48.0 and the scan's 220000.0).
The absolute number is the useful one - 9.6 us per attempt at n=50000 against the
scan's 16566.0 us, 9.5x clear of the 1733.8 us band that row can be charged.

### 3. Agreement, and the boundary cases

```text
               dataset  queries  stored  disagree  items out  tree out
--------------------- -------- ------- --------- ---------- ---------
    sparse n=3000 half      500    3000         0        296       296
    sparse n=3000 incl      500    3000         0        296       296
     dense n=3000 half      500    3000         0        627       627
     dense n=3000 incl      500    3000         0        627       627
     sparse n=400 half      500     400         0        281       281
     sparse n=400 incl      500     400         0        281       281

boundary cases: conflict counts per structure
                          case   open   incl  naive  sweep   tree agree
----------------------------- ------ ------ ------ ------ ------ -----
    touching [0,10) vs [10,20)      0      1      0      0      0   yes
     zero-length query [10,10)      0      2      0      0      0   yes
      zero-length stored [5,5)      0      1      0      0      0   yes
duplicate [0,10) inserted twic      2      2      2      2      2   yes
 contained [2,3) inside [0,10)      1      1      1      1      1   yes

inverted [10,3) insert -> naive:rejected, sweep:rejected, tree:rejected
```

3000 compared query results over 2408 conflict rows, 0 disagreements - and a
reordering counts as a disagreement, not just a different set. The convention is
load-bearing: touching rows give 0 conflicts half-open against 1 inclusive, a
zero-length query 0 against 2. All three reject the inverted row before it can
corrupt an augmented `max_end`.

### 4 and 4b. Write cost, and whether sorted input degenerates the tree

```text
     n tree h naive us/op sweep us/op  tree us/op      band n/s/t  tree desc
----- ------ ----------- ----------- ----------- --------------- ----------
   250     14        3.07       21.10        4.68     0.2/2.0/2.7        7.8
   500     18        4.52       43.76        4.31     0.3/2.7/2.1        9.3
  1000     24        9.53       86.21        4.46     5.0/8.7/0.7       10.7
  2000     25       20.33      184.59        4.30    9.4/13.5/2.5       12.4
  4000     27       38.28      394.67        5.42    9.3/17.4/4.0       13.8
```

```text
      priorities  build order  height  nav/ins tail comps     shape
--------------- ------------ ------- -------- ---------- ---------
          random       sorted      24      8.2         11  balanced
          random     shuffled      29     12.8          6  balanced
 insertion order       sorted    2000    999.5       2000     chain
 insertion order     shuffled      26     11.9         12  balanced
```

The sweep's round trip goes 21.10 -> 394.67 us/op for a 16x larger table because
both legs rebuild the prefix-max array; the tree moves 4.68 -> 5.42 (1.16x) while
its height goes 14 -> 27; the scan's 3.07 -> 38.28 us is delete, not insert -
`list.remove` scans. At n=4000 both write differences clear their own pair bands
(the scan and tree by 32.86 us against bands of 9.26 and 4.02, the sweep and tree
by 389.25 against 17.39 and 4.02), so the sweep is the expensive writer even
though 1b already named it the cheaper reader at n=100000. That the write price
rose 1.16x while the descent it performs rose 1.77x is itself the point: cost
grew by less than the number of comparisons made, so depth is not what the price
is mainly spent on.

The degeneration answer is measured, not assumed: it needs both sorted input and
non-randomized priorities - then height is 2000, 999.5 comparisons per insert and
a tail query that tests every row. With random priorities the same sorted input
gives height 24 and 11 tail tests, and shuffled input under deterministic
priorities is the honest middle case at height 26, nav/ins 11.9 - the 29 in the
table above is random priorities on shuffled input, a different row.

### 5. Bytes per stored interval

```text
                  holder       KiB   bytes/iv   vs naive   vs fresh
----------------------- --------- ---------- ---------- ----------
        fresh tuple list    1419.8       72.7      1.00x      1.00x
       shared tuple list     157.1        8.0      0.11x      0.11x
              naive scan    1419.9       72.7      1.00x      1.00x
            sorted sweep    3117.2      159.6      2.20x      2.20x
           interval tree    3597.6      184.2      2.53x      2.53x
```

n=20000, tracemalloc delta across the build. A fresh tuple list is the 72.7 B/iv
floor and the scan sits on it; the sweep pays 159.6 (2.20x) for a parallel key
array plus the prefix-max array, the tree 184.2 (2.53x) for two child pointers, a
parent pointer, a priority and `max_end`. Reuse drops any holder to 8.0, so most
of the scan's cost is the tuple itself; the tree's delta over it is 2178 KiB for
20000 rows and linear from there.

### 6. Where the structure stops paying (n=20000, widening window)

```text
  wanted   mean_k  naive/cq  sweep/cq  tree/cq   tree/k naive/tree
------- -------- --------- --------- -------- -------- ----------
  0.0005     10.6     20000        11       28     2.67     704.2x
  0.0020     40.2     20000        40       58     1.44     344.8x
  0.0100    197.8     20000       198      215     1.09      92.8x
  0.0500    996.5     20000       996     1014     1.02      19.7x
  0.2000   4002.6     20000      4003     4019     1.00       5.0x
  0.5000  10016.5     20000     10017    10033     1.00       2.0x
  1.0000  20000.0     20000     20000    20000     1.00       1.0x
```

`tree/k` is the giveaway: 2.67 tests per returned conflict at 10.6 conflicts,
1.00 at 4002.6, and at fraction 1.0 all three test 20000 rows (1.0x) - once the
output is the table, no index helps. The sweep undercut the tree on counts even at
the sparse end (11 against 28 in the integer column; 10.63 against 28.40
unrounded, 2.7x fewer), so read this as a density statement, not a ranking.

### 6b. The size at which the tree starts winning

```text
     n q/pass naive us/q sweep us/q  tree us/q       band n/s/t        winner
----- ------ ---------- ---------- ---------- ---------------- -------------
    20   7500       4.47       2.08       2.61   0.86/0.18/0.32         sweep
    50   3000       8.68       2.42       2.82   0.94/0.85/1.35  tie in noise
   100   1500      17.61       2.23       2.71   2.14/1.57/1.20  tie in noise
   200    750      32.07       2.55       2.90   3.81/0.26/1.36  tie in noise
   500    300      81.36       3.13       3.87   4.14/2.99/0.28  tie in noise
  1000    300     161.81       3.33       4.04  11.52/1.20/0.24  tie in noise
  3000    300     506.78       3.79       4.98 139.79/3.68/0.20  tie in noise
```

```text
       n   mean k  naive us/q   tree us/q   pair band  naive/tree
------- -------- ----------- ----------- ----------- -----------
    1000      517      330.23      361.39      163.03       0.91x
    3000     1519     1050.78     1078.64      154.24       0.97x
```

Honest answer to the flip question: the tree is clear of the scan at all 7 sizes,
starting from the smallest one probed (n=20, 2.61 us against 4.47, a 1.86 us lead
over a 0.86 us band), so the flip sits below n=20 and this run cannot locate it.
The `winner` column answers a different question - it compares the two cheapest
structures with each other - and names the sweep only at n=20, with ties at the
other six, so no row hands the tree a measurable read win over its sibling.

The two ratios are not the same claim: 4733.7x fewer tests in section 1 against
101.8x wall clock at n=3000 here (506.78 us against 4.98 us) - the gap is
per-query overhead no index removes: list build, result sort, call overhead.
Widen the window until it returns half the table and the verdict disappears
entirely: 31.16 us apart at n=1000 against a 163.03 us band, 27.86 us at n=3000
against 154.24 us, ratios 0.91x and 0.97x - i.e. neither row separates the scan
from the tree at all. On the write side the sweep pays 72.8x the tree's price per
op (394.67 against 5.42 us at n=4000) to buy a 1.19 us/query read edge.

## How it decides

- Overlap test: `max(a.start, b.start) < min(a.end, b.end)` under half-open, `<=`
  under inclusive. The textbook form `a.lo < b.hi and b.lo < a.hi` misjudges
  zero-length rows, which is why `[5, 5)` costs 0 conflicts half-open.
- `allow_back_to_back=True` selects half-open and `False` inclusive, once per
  index, so a table cannot mix the rules mid-query.
- Validation lives in `make()`: non-numeric or NaN endpoints, booleans, non-string
  labels and inverted ranges raise `IntervalError` before any structure stores the
  row, so a bad interval cannot poison a subtree's `max_end`.
- `count_conflicts` is `len(overlaps(query))` by construction, never a second
  traversal: counts and lists cannot drift apart.
- Pruning is a necessary condition only, and the verdict is delegated to the same
  `overlap()`. The sweep walks its sorted array right-to-left and stops at the
  first prefix whose maximum end no longer reaches the query start; the tree
  descends into a right subtree only when `node.start < hi` and a left one only
  when that subtree's `max_end > lo`.
- `first_free(start, end, horizon)` fixes `duration = end - start` and tries only
  `start` and conflicting ends, jumping to `max(hit ends)` per hop. Those
  candidates are exhaustive because the feasible set is closed under sliding left
  to the nearest stored end; when a hop cannot advance it returns `None`.

## Limitations

- Cost stays output-sensitive and nothing here escapes it: at one density the
  three columns converge on the same 20000 tests, and a wide-window query that
  returns 1519 conflicts at n=3000 gives no verdict at all (27.86 us apart against
  a 154.24 us band).
- The sweep is a bad writer (394.67 us/op at n=4000 against the tree's 5.42, 22.4x
  that pair's band) for a read edge worth at most 1.8 us/query, so it only makes
  sense for bulk, rarely-mutated tables.
- The tree's balance is probabilistic: without randomized priorities, sorted
  booking arrival measured a height-2000 chain.
- 2.53x a bare list's bytes per row is real and linear; at a few hundred rows the
  scan's simplicity is still the better buy, though no size in this run showed it
  winning.
- No time zones or DST - a shift moves your axis, remap before inserting - no
  persistence, no concurrency, and `overlaps` materialises and sorts each result.
- `comparisons` and `nav` exclude the final sort, which is why probe ratios read
  far higher than the wall-clock ratios they imply.

## API

| Name | Kind | Contract |
| --- | --- | --- |
| `make(start, end, label="")` | function | validate + normalise to `(float, float, str)` |
| `convention_for(allow_back_to_back)` | function | `True` -> `HALF_OPEN`, `False` -> `INCLUSIVE` |
| `overlap(a, b, convention)` | function | the one rule every structure delegates to |
| `sort_key(interval)` | function | deterministic order for returned lists |
| `IntervalIndex` | base class | interface + derived `count_conflicts` / `first_free` / counters |
| `NaiveIndex` | class | list scan: O(n) read, O(1) append, O(n) delete |
| `SweepIndex` | class | sorted keys + prefix-max ends: cheapest read, O(n) write |
| `IntervalTree` | class | augmented treap; `randomize=True`, `height()`, `all_intervals()` |
| `index.insert(interval)` | method | validate then store; returns the normalised row |
| `index.delete(interval)` | method | remove one exact row; `False` when absent |
| `index.overlaps(query)` | method | every conflicting row, sorted |
| `index.count_conflicts(query)` | method | `len(overlaps(query))`, duplicates counted |
| `index.first_free(start, end, horizon)` | method | earliest free start for that duration, else `None` |
| `index.query(interval)` | method | normalise a 2- or 3-tuple; fast path for `make()` output |
| `index.bulk_load(intervals)` | method | insert every row, return `self`; the sweep sorts in one pass |
| `index.stats()` / `reset_stats()` | method | `(comparisons, nav)`; `index.jumps` counts `first_free` searches |
| `len(index)` | builtin | stored rows, duplicates included |
| `IntervalError` | exception | subclass of `ValueError`, raised by `make()` and bad conventions |

## Directory tree

```tree
    .gitattributes
    .gitignore
    LICENSE
    README.md
    pyproject.toml
    benchmarks/
        last_run.txt
        run_benchmarks.py
    intervallib/
        __init__.py
        interval.py
        naive.py
        sweep.py
        trace.py
        tree.py
    tests/
        test_interval.py
```

## Reproduce

```bash
python -u -m unittest discover -s tests -p 'test_*.py' -t tests
python -u benchmarks/run_benchmarks.py
```

The benchmark buffers every line through a `_Tee` and writes the identical text to
`benchmarks/last_run.txt` in a `finally`, so the artifact you read is the artifact
that printed. Deterministic sections draw from `random.Random(<fixed seed>)`;
timing sections disable gc while sampling, discard two warm-ups, keep best-of-5,
and print a `band n/s/t` column giving each structure's own worst-minus-best spread
for that pass. A pairwise verdict then uses the wider of the two bands it compares
(`pair_floor`), never the widest number on the row - the scan's variance runs
orders of magnitude above the indexes', so a single global floor would call every
small gap noise and every large one real. Re-running moves the `us` columns and
can move a winner label whose gap sits inside its own pair band; the probe counts,
the agreement table and the degeneration rows should not move. Every number here
belongs to this machine and this run.

## License

MIT, Copyright (c) 2026 sparkx313. See `LICENSE`.
