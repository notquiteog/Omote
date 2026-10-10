"""Bounded, read-only JSON comparison including unknown custom-node fields."""
from __future__ import annotations

import json
from itertools import zip_longest

MISSING = object()


def compare(before, after, *, limit=250):
    changes = []
    counts = {'added': 0, 'removed': 0, 'changed': 0}
    pending = [('', before, after)]

    def preview(value):
        if value is MISSING:
            return None
        text = json.dumps(value, ensure_ascii=False, indent=2)
        return text if len(text) <= 2000 else text[:2000] + '\n… (preview shortened; export the version for the complete value)'

    while pending:
        path, left, right = pending.pop()
        if isinstance(left, dict) and isinstance(right, dict):
            for key in reversed(sorted(left.keys() | right.keys())):
                token = key.replace('~', '~0').replace('/', '~1')
                pending.append((path + '/' + token, left.get(key, MISSING), right.get(key, MISSING)))
            continue
        if isinstance(left, list) and isinstance(right, list):
            pending.extend((path + '/' + str(index), a, b) for index, (a, b) in
                           reversed(list(enumerate(zip_longest(left, right, fillvalue=MISSING)))))
            continue
        if type(left) is type(right) and left == right:
            continue
        kind = 'added' if left is MISSING else 'removed' if right is MISSING else 'changed'
        counts[kind] += 1
        if len(changes) < limit:
            changes.append({'path': path or '/', 'kind': kind, 'before': preview(left), 'after': preview(right)})
    total = sum(counts.values())
    return {'changes': changes, 'counts': counts, 'total': total, 'truncated': total > len(changes)}
