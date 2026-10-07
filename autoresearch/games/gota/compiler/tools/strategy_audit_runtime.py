# Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/tools/strategy_audit_runtime.py
"""Replay the strategy runtime from change-complete telemetry; no gameplay predicates."""
from collections import defaultdict
from dataclasses import replace

from strategy_basic import SelectState, reference_select, reference_priority


def condition(node, flags):
    op = node[0]
    if op == 'always':
        return True
    if op == 'sit':
        return flags[node[1]]
    if op == 'not':
        return not condition(node[1], flags)
    if op == 'and':
        return condition(node[1], flags) and condition(node[2], flags)
    return condition(node[1], flags) or condition(node[2], flags)


def rule_ok(rule, strategy, seat, flags):
    role = not rule.roles or any(seat in strategy.roles[rule.roles[0]][name] for name in rule.roles[1])
    return role and condition(rule.condition, flags)


def reconstruct(strategy, mapping, events, states, seat, end_tick):
    """Return decision evidence and activation windows. States are indexed by (tick, seat).

    PWD is a change log, not a sample. Carry it only up to its 24-tick heartbeat.
    PWB is never carried. Runtime events at respawn close the prior activation at
    the replay death tick. Commands for a decision at t execute at t+1.
    """
    by_tick = defaultdict(list)
    for event in events:
        by_tick[event['t']].append(event)
    rules = {r.code: r for r in strategy.rules}
    priorities = [0] + [r.priority for r in strategy.rules]
    adaptations = {c.code: c for c in strategy.of_kind('A')}
    active_effects = {}
    snapshots = [e['r'] for e in events if e['kind'] == 'PWP' and e['a'] == 0]
    snapshot_ok = sorted(snapshots) == sorted(rules) and all(
        e['p'] == 0 and e['o'] == e['n'] == rules[e['r']].priority
        for e in events if e['kind'] == 'PWP' and e['a'] == 0)
    state = SelectState()
    last_decision, last_alive = None, None
    version = 0
    priority_valid = True
    rows, windows, issues = [], [], []
    window = None
    pending_death = None
    if not snapshot_ok:
        issues.append({'reason': 'initial_priority_snapshot', 't': None})
    alive = [t for t in range(end_tick) if (t, seat) in states and states[t, seat]['hp'] > 0]
    if any((t, seat) not in states for t in range(end_tick + 1)):
        issues.append({'reason': 'replay_state_gap', 't': None})
    alive_set = set(alive)
    for t in by_tick:
        if t not in alive_set:
            issues.append({'reason': 'telemetry_outside_decision_ticks', 't': t})
    for t in alive:
        batch = by_tick[t]
        gap = last_alive is not None and t != last_alive + 1
        if gap:
            pending_death = mapping['codes']['capability'][rules[state.rule].capability] if state.rule and not state.ended else None
            state = SelectState()
            last_decision = None
        updates = [e for e in batch if e['kind'] == 'PWD']
        if len(updates) > 1:
            issues.append({'reason': 'duplicate_decision', 't': t})
        if updates:
            last_decision = updates[-1]
        decision = last_decision
        coverage = bool(decision and t - decision['t'] < 24 and snapshot_ok)
        if not coverage:
            issues.append({'reason': 'telemetry_gap', 't': t})
            rows.append({'t': t, 'status': 'unmeasurable', 'reason': 'telemetry_gap', 'decision': decision})
            last_alive = t
            continue
        transitions = []
        for event in batch:
            if event['kind'] != 'PWP' or event['a'] == 0:
                continue
            adaptation = adaptations[event['a']]
            effect = adaptation.effect
            target = next(r.code for r in strategy.rules if r.id == effect.rule)
            arithmetic_ok = event['r'] == target and event['p'] == version + 1 and event['o'] == priorities[target]
            if event['a'] in active_effects:
                del active_effects[event['a']]
            else:
                active_effects[event['a']] = effect
            effects = []
            for code, item in sorted(active_effects.items()):
                if item.rule == effect.rule:
                    amount = item.amount if isinstance(item.amount, int) else adaptations[code].param(item.amount).value
                    effects.append(('set' if item.op == '=' else 'add', -amount if item.op == '-=' else amount))
            expected_priority = reference_priority(rules[target].priority, effects)
            arithmetic_ok = arithmetic_ok and expected_priority == event['n']
            transitions.append({'t': t, 'adaptation': adaptation.id, 'ok': arithmetic_ok,
                                'expected': expected_priority, 'actual': event['n']})
            priority_valid = priority_valid and arithmetic_ok
            priorities[target] = event['n']
            version = event['p']
        flags = {name: bool(decision['f'][(code - 1) // 31] & (1 << ((code - 1) % 31)))
                 for name, code in mapping['codes']['situation'].items()}
        ok = [False] + [rule_ok(r, strategy, seat, flags) for r in strategy.rules]
        expected = reference_select(state, ok, priorities, t, strategy.commitment)
        expected_events = []
        if pending_death is not None:
            expected_events.append((pending_death, 5))
            pending_death = None
        if expected.preempted:
            expected_events.append((mapping['codes']['capability'][rules[expected.preempted].capability], 4))
        if expected.new:
            expected_events.append((mapping['codes']['capability'][rules[expected.rule].capability], 1))
        cap_events = [e for e in batch if e['kind'] == 'PWE']
        observed_events = [(e['c'], e['e']) for e in cap_events if e['e'] not in (2, 3)]
        finishes = [e for e in cap_events if e['e'] in (2, 3)]
        cap_code = mapping['codes']['capability'][rules[expected.rule].capability] if expected.rule else 0
        finish_ok = len(finishes) <= 1
        for event in finishes:
            comp = strategy.components[rules[expected.rule].capability] if expected.rule else None
            finish_ok = finish_ok and event['c'] == cap_code and bool(comp) and any(
                c.code == event['k'] and c.kind == ('done' if event['e'] == 2 else 'abort') for c in comp.conditions)
        event_ok = observed_events == expected_events and finish_ok
        selection_ok = expected.rule == decision['r'] and expected.held == decision['h']
        priority_ok = priority_valid and decision['p'] == version
        selected = rules.get(decision['r'])
        role_ok = not selected or not selected.roles or any(
            seat in strategy.roles[selected.roles[0]][name] for name in selected.roles[1])
        rows.append({'t': t, 'status': 'pass' if selection_ok and event_ok and priority_ok else 'fail',
                     'reason': None, 'selection_ok': selection_ok, 'events_ok': event_ok,
                     'priority_ok': priority_ok, 'role_ok': role_ok, 'expected_rule': expected.rule,
                     'expected_held': expected.held, 'decision': decision, 'flags': flags,
                     'transitions': transitions})
        # Window boundaries follow recorded activations, independently of expected selection.
        for event in cap_events:
            if event['e'] in (2, 3, 4, 5) and window:
                if event['c'] != window['capability']:
                    window.update(end=t, end_reason='event_mismatch')
                elif window['end'] is None:
                    window.update(end=t + 1 if event['e'] in (2, 3) else t,
                                  end_reason={2: 'done', 3: 'abort', 4: 'preempted', 5: 'died'}[event['e']])
                windows.append(window)
                window = None
            if event['e'] == 1:
                if window:
                    if window['end'] is None:
                        window.update(end=t, end_reason='missing_end')
                    windows.append(window)
                window = {'start': t, 'end': None, 'capability': event['c'], 'end_reason': None}
        if window and (t + 1, seat) in states and states[t + 1, seat]['hp'] <= 0:
            window.update(end=t + 1, end_reason='died')
        if finishes:
            expected = replace(expected, ended=True)
        state = expected
        last_alive = t
    if window:
        if window['end'] is None:
            window.update(end=end_tick, end_reason='truncated')
        windows.append(window)
    return {'ticks': rows, 'windows': windows, 'issues': issues}
