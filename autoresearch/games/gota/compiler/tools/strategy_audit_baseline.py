# Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/tools/strategy_audit_baseline.py
"""Reviewed M1 predicates, bound to exact source and check text; never infer prose semantics."""
from collections import defaultdict
import math

from pw_intent import observed_view
from strategy_audit import binding
from strategy_format import REF_RE, condition_refs
from pw_scout import wilson

SHOUT_STEP_OFFSET = 1  # decision t -> recorded shout at t+1; also tested with wrong offsets
SHOT_DISTANCE_BINS_CM = (0, 1000, 2000, 4000, math.inf)


def i32(value):
    return (value + 2**31) % 2**32 - 2**31


def div(value, by):
    return (abs(value) // abs(by)) * (-1 if (value < 0) != (by < 0) else 1)


def squad_targets(ep, seat, states, params):
    """Rebuild persistent avoid memory from replay tick zero, including death gaps."""
    hearts = {h['idx']: h['pos'] for h in ep.meta['hearts']}
    history = defaultdict(dict)
    for row in ep['heart_states'].to_dict('records'):
        history[row['t']][row['heart']] = row
    hx, hy = ep.meta['homes'][seat % 2]
    squad = (seat // 2 % 8) // 4
    objective, px, py = 0, 0, 0
    avoid = [0] * 64
    result = {}
    for t in range(ep.summary['ticks']):
        if (t, seat) not in states or len(history[t]) != len(hearts):
            break  # Unrecoverable history gap: this memory never resets on death.
        me = states[t, seat]
        if me['hp'] <= 0:
            continue
        x, y = int(me['x']), int(me['z'])
        if t % params['progress_period'] == 0:
            distance = i32((x - px)**2 + (y - py)**2)
            if distance < params['stall_sq'] and 0 <= objective < 64:
                ox, oy = hearts.get(objective, (-1, -1))
                if i32((ox - x)**2 + (oy - y)**2) > params['far_sq']:
                    avoid[objective] = t + params['avoid_ticks']
            px, py = x, y
        objective, other = -1, -1
        for turn in (0, 1):
            ref_y = hy + (-params['ref_offset'] if turn == 0 else params['ref_offset'])
            if seat % 2:
                ref_y = params['mirror_y'] - ref_y
            choice, cost_best = -1, 2**31 - 1
            for j in sorted(hearts):
                if j >= 64 or history[t][j]['owner'] == seat % 2 or j == other:
                    continue
                ox, oy = hearts[j]
                dx, dy = div(ox - hx, 8), div(oy - ref_y, 8)
                cost = i32(dx * dx + dy * dy)
                if history[t][j]['owner'] == -1:
                    cost = i32(cost - params['neutral_bonus'])
                if turn == squad and avoid[j] > t:
                    cost = i32(cost + params['avoid_penalty'])
                if cost < cost_best:
                    choice, cost_best = j, cost
            if turn == squad:
                objective = choice
            elif turn < squad:
                other = choice
        if objective < 0 and other >= 0:
            objective = other
        result[t] = objective
    return result


def contact_truth(ep, seat, t, states, visibility, target, range_sq):
    bodies = visibility.get((t, seat))
    if bodies is None or any((t, int(b)) not in states for b in set(bodies) | {seat}):
        return None, {'reason': 'replay_visibility_gap'}
    positions = {int(b): (int(states[t, int(b)]['x']), int(states[t, int(b)]['z'])) for b in set(bodies) | {seat}}
    disguised = {int(b): bool(states[t, int(b)]['disguised']) for b in set(bodies) | {seat}}
    view = observed_view(seat, bodies, disguised, positions, 16, ep.meta['rules'] >= 27)
    if target == -1:
        return None, {'reason': 'no_target'}
    body = view.get(target)
    if body is None:
        return False, {'reason': 'target_not_visible', 'target': target}
    ox, oy = positions[seat]
    tx, ty = positions[body]
    distance_sq = i32((tx - ox)**2 + (ty - oy)**2)
    return target % 2 != seat % 2 and distance_sq <= range_sq, {
        'target': target, 'body': body, 'body_is_ally': body % 2 == seat % 2,
        'distance_sq': distance_sq}


def conditional_result(outcome, *, belief_correct, execution_correct, complete):
    """Truth values are True/False/None. Unknown prerequisites are never success."""
    if not complete:
        return 'unmeasurable', 'truncated_activation'
    if belief_correct is None or execution_correct is None:
        return 'unmeasurable', 'upstream_unmeasurable'
    if not belief_correct or not execution_correct:
        return 'not_exercised', 'upstream_failed'
    return 'measured', None  # The baseline declares statistics, not acceptance thresholds.



def semantics_match(component, strategy, mapping, reference):
    """Bind a reviewed predicate to its source dependency closure, not unrelated behavior."""
    current, old = mapping['components'], reference['components']
    adaptations = {key: item['text_hash'] for key, item in current.items() if item['kind'] == 'A'}
    prior_adaptations = {key: item['text_hash'] for key, item in old.items() if item['kind'] == 'A'}
    if adaptations != prior_adaptations:
        return False  # Adaptations can alter priorities without appearing in Uses.
    pending, visited = [component], set()
    while pending:
        key = pending.pop()
        if key in visited:
            continue
        visited.add(key)
        if key not in current or key not in old or current[key]['text_hash'] != old[key]['text_hash']:
            return False
        comp = strategy.components[key]
        if comp.kind == 'SK' and current[key]['unit_sha256'] != old[key]['unit_sha256']:
            return False
        refs = set(comp.uses) | set(REF_RE.findall(comp.compiled_text))
        if comp.kind == 'ST' or any(ref.startswith('R.') for ref in refs):
            # ST components do not declare Uses for their selection dependencies.
            refs.update(name for name in current if name.startswith('ST.'))
            for rule in strategy.rules:
                refs.add(rule.capability)
                refs.update(condition_refs(rule.condition))
                refs.update(arg.ref for arg in rule.args.values() if arg.ref)
        pending.extend(ref for ref in refs if not ref.startswith('R.'))
    return True


def audit_seat(ep, seat, states, events, runtime, issues, strategy, mapping, reference, evidence):
    eid = ep.episode_id
    tick_rows = {r['t']: r for r in runtime['ticks']}
    alive = sorted(tick_rows)
    beliefs = defaultdict(list)
    for event in events:
        if event['kind'] == 'PWB':
            beliefs[event['k'], event['t']].append(event)
    visibility = {(int(r.t), int(r.seat)): set(int(b) for b in r.sees) for r in ep['visibility'].itertuples()}
    targets = None
    raw = []
    for key, check in evidence.checks.items():
        component = check['component']
        comp = strategy.components[component]
        if mapping.get('comms') and comp.kind == 'COM':
            continue  # Codec checks have their own source-bound evaluator.
        supported = semantics_match(component, strategy, mapping, reference) and any(binding(component, c) == check['binding']
                                             for c in reference['components'].get(component, {}).get('checks', []))
        def add(t, status, reason=None, **detail):
            evidence.add(key, eid, seat, t, status, reason, **detail)
        if not supported:
            add(None, 'unmeasurable', 'no_evaluator_for_check_text_or_spec')
            continue
        if issues:
            add(None, 'unmeasurable', issues[0]['reason'], issues=issues)
            continue
        level = check['level']
        if comp.kind == 'K' and comp.log:
            for t in alive:
                if t % comp.log.every != mapping['components'][component]['log_offset']:
                    continue
                samples = beliefs[comp.code, t]
                if len(samples) != 1:
                    add(t, 'unmeasurable', 'belief_sample_missing_or_duplicate')
                    continue
                values = dict(zip(comp.log.fields, samples[0]['d']))
                if level == 'Believed':
                    valid = component != 'K.squad_target' or -1 <= values['objective'] < len(ep.meta['hearts'])
                    add(t, 'pass' if valid else 'fail', values=values)
                elif component == 'K.squad_target':
                    if targets is None:
                        targets = squad_targets(ep, seat, states, {p.name: p.value for p in comp.params})
                    if t not in targets:
                        add(t, 'unmeasurable', 'replay_history_gap')
                    else:
                        add(t, 'pass' if values['objective'] == targets[t] else 'fail',
                            expected=targets[t], actual=values['objective'])
                elif component == 'K.contacts':
                    valid, detail = contact_truth(ep, seat, t, states, visibility, values['best'], comp.param('range_sq').value)
                    reason = detail.pop('reason', None)
                    add(t, ('not_exercised' if reason == 'no_target' else 'unmeasurable') if valid is None
                        else ('pass' if valid else 'fail'), reason, **detail)
                else:
                    add(t, 'unmeasurable', 'remembered_kind_and_position_not_logged',
                        nearest=values['nearest'], temporal_criterion='was at has no time bound')
        elif component == 'K.self_motion':
            add(None, 'unmeasurable', 'belief_not_logged')
        elif comp.kind == 'S' or component in ('ST.rules', 'ST.commitment', 'ST.roles') or (comp.kind == 'C' and level == 'Acted'):
            for t, row in tick_rows.items():
                if row['status'] == 'unmeasurable':
                    add(t, 'unmeasurable', row['reason'])
                    continue
                if comp.kind == 'S':
                    add(t, 'pass', flag=row['flags'][component])
                elif component == 'ST.roles':
                    add(t, 'pass' if row['role_ok'] else 'fail', rule=row['decision']['r'])
                elif component == 'ST.commitment':
                    add(t, 'pass' if row['selection_ok'] else 'fail', expected=row['expected_rule'],
                        actual=row['decision']['r'], held=row['decision']['h'], expected_held=row['expected_held'])
                elif component == 'ST.rules':
                    add(t, row['status'], expected=row['expected_rule'], actual=row['decision']['r'],
                        events_ok=row['events_ok'], priorities_ok=row['priority_ok'])
                else:
                    code = mapping['codes']['capability'][component]
                    expected_cap = next((mapping['codes']['capability'][r.capability] for r in strategy.rules
                                         if r.code == row['expected_rule']), 0)
                    if code in (expected_cap, row['decision']['c']):
                        add(t, 'pass' if row['selection_ok'] else 'fail', expected=expected_cap, actual=row['decision']['c'])
        elif component == 'COM.status_call':
            texts = {'Contact! Cover this lane.', 'Too many. Falling back.', 'Moving with the squad.'}
            shouts = [r for r in ep['shouts'].to_dict('records') if r['seat'] == seat and r['text'] in texts]
            by_tick = defaultdict(list)
            for row in shouts:
                by_tick[row['t'] - SHOUT_STEP_OFFSET].append(row)
            for t in sorted(set(t for t in alive if t % 360 == seat * 21) | set(by_tick)):
                if t + SHOUT_STEP_OFFSET > ep.summary['ticks']:
                    add(t, 'unmeasurable', 'after_episode_end')
                    continue
                expected = t in tick_rows and t % 360 == seat * 21
                add(t, 'pass' if len(by_tick[t]) == int(expected) else 'fail',
                    expected_count=int(expected), actual_count=len(by_tick[t]), execution_tick=t + SHOUT_STEP_OFFSET)
        elif level == 'Result' and comp.kind == 'C':
            code = mapping['codes']['capability'][component]
            for window in runtime['windows']:
                if window['capability'] != code:
                    continue
                start, end = window['start'], window['end']
                complete = window['end_reason'] not in ('truncated', 'missing_end', 'event_mismatch')
                # Raw outcomes retain their causal limitation instead of passing an empty upstream gate.
                if component == 'C.fall_back':
                    outcome = None if not complete else all(states.get((t, seat), {}).get('hp', 0) > 0
                                                                          for t in range(start + 1, end + 1))
                elif component == 'C.resupply':
                    pickups = [r for r in ep['pickups'].to_dict('records') if r['seat'] == seat and start < r['t'] <= end]
                    outcome = None if any(r['ambiguous'] for r in pickups) else bool(pickups)
                else:
                    outcome = None  # Target can change between belief samples without an input change.
                samples = [e for e in events if e['kind'] == 'PWB' and
                           e['k'] == mapping['codes']['knowledge']['K.squad_target'] and start <= e['t'] < end]
                sampled_targets = sorted({e['d'][0] for e in samples if e['d'][0] >= 0})
                captures = [r for r in ep['captures'].to_dict('records') if r['kind'] == 'capture_complete' and
                            r['team'] == seat % 2 and start < r['t'] <= end and r['heart'] in sampled_targets]
                raw.append({'episode': eid, 'seat': seat, 'component': component, **window, 'outcome': outcome,
                            'sampled_targets': sampled_targets, 'captures_of_sampled_targets': len(captures),
                            'reason': 'target_not_exact' if component == 'C.take_heart' else None})
                status, reason = conditional_result(outcome, belief_correct=None, execution_correct=None, complete=complete)
                add(start, status, reason, end=end, raw_outcome=outcome)
        elif component == 'SK.motor' and level == 'Result':
            shots = [r for r in ep['shots'].to_dict('records') if r['seat'] == seat]
            raw.append({'episode': eid, 'seat': seat, 'component': component, 'shots': len(shots),
                        'hits': sum(bool(r['hit']) for r in shots), 'metric': 'hit_rate',
                        'no_inferred_target': sum(r['aim_target_distance'] is None or
                                                  not math.isfinite(float(r['aim_target_distance'])) for r in shots),
                        'rate': sum(bool(r['hit']) for r in shots) / len(shots) if shots else None,
                        'wilson95': wilson(sum(bool(r['hit']) for r in shots), len(shots)),
                        'uncertainty_unit': 'shot; descriptive only, shots within an episode are correlated'})
            for low, high in zip(SHOT_DISTANCE_BINS_CM, SHOT_DISTANCE_BINS_CM[1:]):
                group = [r for r in shots if r['aim_target_distance'] is not None and
                         low <= float(r['aim_target_distance']) < high]
                hits = sum(bool(r['hit']) for r in group)
                raw.append({'episode': eid, 'seat': seat, 'component': component, 'metric': 'hit_rate_by_inferred_target_range',
                            'range_min_cm': low, 'range_max_cm': None if math.isinf(high) else high,
                            'shots': len(group), 'hits': hits, 'wilson95': wilson(hits, len(group))})
            for shot in shots:
                add(int(shot['t']), 'unmeasurable', 'motor_execution_unmeasurable', raw_hit=bool(shot['hit']))
        else:
            add(None, 'unmeasurable', 'criterion_undefined' if component == 'C.cover_heart' else 'private_state_not_logged')
    return raw
