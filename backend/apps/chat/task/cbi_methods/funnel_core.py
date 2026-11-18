"""Extracted unchanged function bodies from CBI-METHODS-01 v0.2. Research only."""

def normalize(events):
    seen = {}
    for event in events:
        if event['id'] in seen and seen[event['id']] != event:
            raise ValueError('conflicting_event_id')
        seen[event['id']] = event
    return list(seen.values())

def observed_funnel(events, start, end, window, observation_asof):
    """Reference only: fixed anchor, 3 ordered event types, one namespace.

    A strict path is sufficient positive evidence. Relaxed equal-time ordering
    is used only to detect whether unresolved precision could change reach.
    It is never counted as a verified conversion.
    """
    grouped = {}
    for event in normalize(events):
        if event['t'] <= observation_asof:
            grouped.setdefault(event['attempt'], []).append(event)
    rows = []
    for key, group in grouped.items():
        views = [e['t'] for e in group if e['event'] == 'view']
        if not views:
            continue
        anchor = min(views)
        if not start <= anchor < end:
            continue
        deadline = anchor + window
        def path(allow_equal):
            last, reached, matching = anchor, 1, [('view', anchor)]
            for name in ('cart', 'pay'):
                candidates = [e['t'] for e in group if e['event'] == name
                    and (e['t'] >= last if allow_equal else e['t'] > last)
                    and e['t'] <= deadline]
                if not candidates:
                    break
                last, reached = min(candidates), reached + 1
                matching.append((name, last))
            return reached, matching
        strict_reached, strict_path = path(False)
        possible_reached, _ = path(True)
        rows.append({'attempt': key, 'anchor': anchor, 'deadline': deadline,
                     'reached': strict_reached, 'possible_reached': possible_reached,
                     'ambiguous': possible_reached > strict_reached,
                     'mature_by_time': deadline <= observation_asof,
                     'strict_path': strict_path})
    return sorted(rows, key=lambda r: r['attempt'])
