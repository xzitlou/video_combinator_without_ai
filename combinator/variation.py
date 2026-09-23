"""How variants are combined, ordered for publishing and compared with each other.

Platforms push down near-duplicate videos, so the product steers toward variants that differ
in substance. Pure functions over tuples of (hook, body, closer); closer may be None. Anything
hashable works as a clip (model instances in the app, strings in tests).
"""

from itertools import product

SLOT_NAMES = ("gancho", "contenido", "cierre")


def all_combinations(hooks, bodies, closers):
    """Every hook × body × closer. Without closers each video is just hook + body."""
    return list(product(hooks, bodies, closers or [None]))


def distinct_combinations(hooks, bodies, closers):
    """Each hook+body pair exactly once, closers rotating so each is used evenly.

    Avoids the riskiest near-duplicates: same hook and same body with only the closer swapped.
    """
    if not closers:
        return all_combinations(hooks, bodies, closers)
    return [
        (hook, body, closers[(i + j) % len(closers)])
        for i, hook in enumerate(hooks)
        for j, body in enumerate(bodies)
    ]


def combination_count(mode, n_hooks, n_bodies, n_closers):
    if mode == "distinct":
        return n_hooks * n_bodies
    return n_hooks * n_bodies * max(n_closers, 1)


def _shared_slots(a, b):
    return sum(1 for x, y in zip(a, b) if x is not None and x == y)


def _pair_cost(a, b, distance):
    # Squared, so sharing two segments costs more than twice sharing one; same hook + body
    # (only the closer differs, the near-duplicate case) gets a large extra penalty so that
    # keeping those apart wins over avoiding a shared hook. Neighbours count 3×, two apart 1×.
    cost = _shared_slots(a, b) ** 2 + (6 if a[:2] == b[:2] else 0)
    return cost * (3 if distance == 1 else 1)


def _boundary_cost(order, i, j, reversed_):
    """Cost of the pairs whose members change when order[i..j] is reversed."""
    n = len(order)

    def at(p):
        return order[i + j - p] if reversed_ and i <= p <= j else order[p]

    pairs = {(i - 1, i), (i - 2, i), (i - 1, i + 1), (j, j + 1), (j - 1, j + 1), (j, j + 2)}
    return sum(
        _pair_cost(at(a), at(b), b - a)
        for a, b in pairs
        if 0 <= a < b < n and not (i <= a and b <= j)  # pairs fully inside keep their cost
    )


def _total_cost(order):
    return sum(
        _pair_cost(order[p], order[p + gap], gap) for gap in (1, 2) for p in range(len(order) - gap)
    )


def _greedy(combos):
    """Always take the remaining video with least in common with the last two."""
    remaining = list(combos)
    order = [remaining.pop(0)]
    while remaining:
        def cost(k):
            total = _pair_cost(remaining[k], order[-1], 1)
            if len(order) > 1:
                total += _pair_cost(remaining[k], order[-2], 2)
            return (total, k)

        order.append(remaining.pop(min(range(len(remaining)), key=cost)))
    return order


def _rounds(combos):
    """Visit every hook+body pair once per round, a different closer each time.

    Videos that differ only in the closer end up a whole round apart, which the greedy walk
    can't always find on its own (e.g. 2 hooks × 2 bodies × 3 closers).
    """
    groups = {}
    for combo in combos:
        groups.setdefault(combo[:2], []).append(combo)
    if all(len(g) == 1 for g in groups.values()):
        return list(combos)
    pairs = _improve(_greedy([(h, b, None) for h, b in groups]))
    order = []
    for r in range(max(len(g) for g in groups.values())):
        for index, (h, b, _) in enumerate(pairs):
            group = groups[(h, b)]
            if r < len(group):
                order.append(group[(r + index) % len(group)])
    return order


def _improve(order):
    """2-opt: reverse stretches of the walk while that lowers the cost of nearby pairs."""
    order = list(order)
    n = len(order)
    improved = True
    while improved:
        improved = False
        for i in range(n - 1):
            for j in range(i + 1, n):
                if _boundary_cost(order, i, j, True) < _boundary_cost(order, i, j, False):
                    order[i:j + 1] = reversed(order[i:j + 1])
                    improved = True
    return order


def publication_order(combos):
    """Reorder so nearby videos (neighbours and two apart) share as little as possible.

    Builds two candidate walks, a greedy one and one that goes round the hook+body pairs, polishes
    both with 2-opt and keeps the cheaper. Not guaranteed optimal. Deterministic; well under a
    second for the ≤100 videos of a project.
    """
    combos = list(combos)
    if not combos:
        return []
    candidates = [_improve(_greedy(combos)), _improve(_rounds(combos))]
    return min(candidates, key=_total_cost)


def similarity(combos, duration):
    """For each combo, how close it is to its siblings.

    `duration(clip)` gives a clip's length (None if not known yet; then every segment weighs the
    same). Returns a list aligned with `combos` of dicts with a level, the index of the sibling
    the verdict refers to, the percent of this video's duration shared with it and the slots
    that differ. Level is "high" when some sibling differs only in the closer (the case platforms
    are most likely to treat as a repeat); then that sibling is reported. Otherwise it is "low"
    and the sibling sharing the most duration is reported.
    """
    clips = {c for combo in combos for c in combo if c is not None}
    known = all(duration(c) for c in clips)
    weight = (lambda c: duration(c)) if known else (lambda c: 1)

    def compare(a, b):
        total = sum(weight(c) for c in a if c is not None) or 1
        shared = sum(weight(x) for x, y in zip(a, b) if x is not None and x == y)
        differs = [name for name, x, y in zip(SLOT_NAMES, a, b) if x != y]
        return round(100 * shared / total), differs

    results = []
    for i, a in enumerate(combos):
        best = None
        for j, b in enumerate(combos):
            if i == j:
                continue
            percent, differs = compare(a, b)
            level = "high" if differs == ["cierre"] else "low"
            key = (level == "high", percent)
            if best is None or key > best[0]:
                best = (key, {"percent": percent, "nearest": j, "differs": differs, "level": level})
        results.append(best[1] if best else {"percent": 0, "nearest": None, "differs": [], "level": "low"})
    return results
