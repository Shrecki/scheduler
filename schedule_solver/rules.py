import math
import random
from collections import Counter
from dataclasses import dataclass
import numpy as np

from ortools.sat.python import cp_model
from model.task_definitions import TASKS
from model.tasks import Category


#############
# RULES
#############

def _by_label(blocks, label):
    counts = Counter()
    for a, k in blocks.items():
        counts[label(a)] += k
    return counts


def orderable(blocks, label=lambda a: a):
    """No two blocks with the same label in a row is possible iff the commonest label has
    <= ceil(N/2) blocks. A task's label is its keep_apart group if it has one, else the task
    itself, so this covers both 'no task twice in a row' and the keep_apart groups."""
    counts = _by_label(blocks, label)
    n = sum(counts.values())
    return n == 0 or max(counts.values()) <= (n + 1) // 2


def quick_order(blocks, label=lambda a: a):
    """Any valid order: repeatedly take the commonest label that isn't the last one, then
    fill each slot of a group with that group's commonest remaining task."""
    left, out = _by_label(blocks, label), []
    while +left:
        g = max((g for g in +left if not out or g != out[-1]), key=left.get)
        out.append(g)
        left[g] -= 1
    members = {}
    for a, k in blocks.items():
        members.setdefault(label(a), Counter())[a] += k
    seq = []
    for g in out:
        a = max(+members[g], key=members[g].get)
        members[g][a] -= 1
        seq.append(a)
    return seq
 
 
def place_calibrations(order, dur, calib, every, max_calib):
    """Fallback only: opening 'CAL', then each next one at the nearest block edge to
    `every` s after the previous ends, at most `max_calib` in total."""
    seq, t, due, cals = ["CAL"], calib, calib + every, max_calib - 1
    for a in order:
        if cals and due <= t + dur[a] / 2:
            seq.append("CAL")
            t, cals = t + calib, cals - 1
            due = t + every
        seq.append(a)
        t += dur[a]
    return seq
 
 
def best_order(blocks, dur, session_budget, task_type, pairs, firsts, n, S, K, rng, apart=(), pair_min=2, repeat_min=1.0, calib=120, spread_w=0.25, time_limit=2.0):
    """CP-SAT: order the chosen blocks and place the K-1 non-opening calibrations.
    Returns the sequence ('CAL' = calibration), or None if no solution was found."""
    names, N = sorted(+blocks), sum(blocks.values())
    P, nbar = range(N), sum(n.values()) / len(n)
    m = cp_model.CpModel()
    z = {(p, a): m.new_bool_var("") for p in P for a in names}   # p holds task a
    e = {p: m.new_bool_var("") for p in P if p}                  # CAL before p (p>0)
    D = [sum(dur[a] * z[p, a] for a in names) for p in P]        # duration at p
    L = [m.new_int_var(0, sum(dur[a] * blocks[a] for a in names), "") for p in P]
    dev = [m.new_int_var(0, 10**6, "") for p in P]
    primary, variety = [], []
 
    for p in P:                                                  # (H1) one per slot
        m.add_exactly_one(z[p, a] for a in names)
    for a in names:
        m.add(sum(z[p, a] for p in P) == blocks[a])              # (H2) all placed
        for p in P[:-1]:
            m.add_bool_or([~z[p, a], ~z[p + 1, a]])              # (H3) no repeat
    m.add(sum(e.values()) <= K - 1)                              # (H4) CAL budget
    for group in apart:                                          # (H5) keep_apart:
        members = [a for a in names if a in group]               #  at most one group
        if len(members) > 1:                                     #  block in any two
            for p in P[:-1]:                                     #  neighbouring slots
                m.add(sum(z[p, a] + z[p + 1, a] for a in members) <= 1)
 
    m.add(L[0] == D[0])                                          # (d) window length
    for p in P[1:]:
        m.add(L[p] == D[p]).only_enforce_if(e[p])
        m.add(L[p] == L[p - 1] + D[p]).only_enforce_if(~e[p])
    for p in P[:-1]:                                             # (d) |window - S|
        m.add(dev[p] >= L[p] - S).only_enforce_if(e[p + 1])      #     when it closes
        m.add(dev[p] >= S - L[p]).only_enforce_if(e[p + 1])
    m.add(dev[-1] >= L[-1] - S)                                  # last: only too long
    primary += dev

    start = [m.new_int_var(0, 10**6, "") for p in P]             # start time of p
    m.add(start[0] == calib)                                     # after opening CAL
    for p in P[1:]:
        m.add(start[p] == start[p - 1] + D[p - 1] + calib * e[p])
    spread = []                                                  # same task spread out:
    for a in names:                                              # shortfall of each pair
        if blocks[a] > 1:                                        # of its blocks against
            gap = (session_budget - calib - dur[a]) // blocks[a] # an even spread
            for p in P:
                for q in P[p + 2:]:
                    sh = m.new_int_var(0, gap, "")
                    m.add(sh >= gap - (start[q] - start[p])).only_enforce_if([z[p, a], z[q, a]])
                    spread.append(sh)
 
    for cat in {task_type[a] for a in names} - {Category.VIDEO_GAME}:   # (a)
        u = [sum(z[p, a] for a in names if task_type[a] == cat) for p in P]
        for p in P[:-1]:
            s = m.new_bool_var("")
            m.add(s >= u[p] + u[p + 1] - 1)
            primary.append(60 * pair_min * s)
 
    for a in names:                                              # variety
        off = m.new_int_var(0, N * N, "")                        # centred (ABBA)
        m.add_abs_equality(off, sum((N - 1 - 2 * p) * z[p, a] for p in P))
        variety += [10 * off, 10 * firsts[a] * z[0, a]]          # rotate openers
    for i, a in enumerate(names):                                # repeated neighbours:
        for b in names[i + 1:]:                                  # either order, each time
            seen = pairs[a, b] + pairs[b, a]                     # they were neighbours in
            w = round(60 * repeat_min * seen * nbar ** 2 / (n[a] * n[b]))  # earlier sessions,
            if w:                                                # vs chance, in seconds
                for p in P[:-1]:                                 # of calibration drift
                    t = m.new_bool_var("")
                    m.add_bool_or([~z[p, a], ~z[p + 1, b], t])
                    m.add_bool_or([~z[p, b], ~z[p + 1, a], t])
                    primary.append(w * t)
    variety += [rng.randint(0, 9) * v for v in z.values()]
 
    m.minimize(session_budget * sum(primary) + round(session_budget * spread_w) * sum(spread)
               + sum(variety))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.random_seed = rng.randint(0, 10**6)
    if solver.solve(m) not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    seq = ["CAL"]                                                # (e) opening CAL
    for p in P:
        if p and solver.value(e[p]):
            seq.append("CAL")
        seq += [a for a in names if solver.value(z[p, a])]
    return seq
 
 
def schedule(tasks_individual_durations: dict[str,int], tasks_repetitions: dict[str,int], tasks_priorities: dict[str, int], task_type: dict[str,Category], n_sessions: int=6, time_budget_per_session: int=105*60, n_calib_per_session: int=5, calib_time_spacing:int=20*60, calib_duration: int=2*60, mandatory_min_repetitions_per_session: dict[str,int]={}, is_mri: bool=False, fmap_duration: int=30, exempt_from_fmap: list[str]|None=None, new_task_blocks: int=1, keep_apart: list[list[str]]|None=None, repeat_pair_min: float=1.0, spread_weight: float=0.25, seed=0):
    """
    Given task and session specifications, design a schedule to best satisfy all constraints.
 
    Conceptually, the algorithm maintains queues per each specified task, with associated priorities.
    Each session reserves time for K = min(floor(T / calib_time_spacing), n_calib_per_session)
    calibrations (the opening one included), leaving B = T - K * calib_duration for blocks.
    Block durations include their fieldmap when is_mri.
    While scheduling the Nth session, it chooses blocks according to a weighted fair queueing scheme:
    - pacing: each video game follows its own timeline, floor(n * (s + 1) / n_sessions + phase)
      blocks done by the end of session s, with phases spread over games of equal n so they take
      turns between sessions. A game's share for a session is what its timeline says is due
      (0 when it is ahead). Any other task's share is ceil(remaining blocks / sessions left).
    - seed: the mandatory blocks, plus 1 block of each of the 2 video games most due
    - fill: tasks below their share are served first: the category (task_type) furthest behind in
      time, then the task with the smallest part of its share already in the session (so new
      tasks come before repeats), then the one whose own queue is furthest behind. Shares are
      exceeded only when no task below its share fits. A game repeats within a session only
      when its own timeline calls for it (then it competes like any due block) or, last of
      all, when nothing else fits (each game key, e.g. 'AI GameStore -game1', counts as its
      own game). Games enter and grow 1 block at a time, any other new task enters
      with new_task_blocks (1 when that doesn't fit). Once 2 video games are in the session,
      games lose their priority edge. A block is accepted only if the blocks still fit B and
      can still be ordered with no task twice in a row and no keep_apart group neighbours.
    Once the blocks are chosen, CP-SAT orders them and places the K - 1 remaining calibrations:
    - hard: every block placed exactly once, one block per position (no overlap, no gap)
    - hard: no task twice in a row (calibrations don't break a repeat)
    - hard: no two blocks of the same keep_apart group next to each other (calibrations don't
      separate them either); the block choice above already guarantees this is possible
    - hard: at most K - 1 calibrations besides the opening one (so the session fits T)
    - soft: each calibration window (end of one calibration to start of the next) close to
      calib_time_spacing; the last window only penalised if too long
    - soft: no two neighbouring blocks of the same category, video games exempt (calibrations
      don't separate them)
    - soft: two tasks that were neighbours in earlier sessions (in either order) not put next
      to each other again; each earlier time costs repeat_pair_min minutes of calibration
      drift, scaled by how often the two would meet by chance
    - soft: blocks of the same task spread through the session. A task with k blocks aims for
      (session length - its duration) / k between their starts; each minute two of its blocks
      start closer than that costs spread_weight minutes of calibration drift
    - soft, weaker: each task's blocks centred in the session, openers rotated, a small random
      term for variety
    Every session opens with a calibration.
 
    Args:
        tasks_individual_durations (dict[str,int]): a dictionary mapping each task to be included to a duration in seconds
        tasks_repetitions (dict[str,int]): a dictionary mapping each task to how many times we want the task repeated over all sessions in total
        tasks_priorities (dict[str, int]): a dictionary mapping each task to a priority to be chosen (higher number is higher priority). Only breaks ties in the fair queueing.
        task_type (dict[str, Category]): a dictionary, mapping each task to a task Category type. Categories are balanced against each other in every session; Category.VIDEO_GAME tasks get the 2-games seed and are kept from repeating within a session.
        n_sessions (int, optional): Number of sessions to schedule. Defaults to 6.
        time_budget_per_session (int, optional): The desired session length, given in seconds. Defaults to 105*60 seconds.
        n_calib_per_session (int, optional): Maximum number of eyetracker calibrations per session, the opening one included. Defaults to 5.
        calib_time_spacing (int, optional): Ideally, the time from the end of one eyetracker calibration to the start of the next, in seconds. Defaults to 20*60.
        calib_duration (int, optional): The duration of an eyetracking episode, in seconds. Defaults to 2*60.
        mandatory_min_repetitions_per_session (dict[str,int], optional): a dictionary, indicating specific tasks where a set minimum number of repetitions for each session is desired. Use it if you want to have e.g. a specific localizer always present in all sessions. The task's total in tasks_repetitions must then be at least this minimum times n_sessions. Defaults to {}.
        is_mri (bool, optional): a boolean indicating whether the modality is MRI or not. If MRI, after every task not exempt from fieldmaps, a fieldmap block is appended. Defaults to False.
        fmap_duration (int, optional): the duration, in seconds, of a single fieldmap block. Defaults to 30.
        exempt_from_fmap (list[str] | None, optional): a list of task keys for which a fieldmap block should NOT BE added. Defaults to None.
        new_task_blocks (int, optional): how many blocks a non-game task gets when it first enters a session. 1 gives the most varied sessions; 2 brings back pairs of each task. Defaults to 1.
        keep_apart (list[list[str]] | None, optional): groups of tasks that must never be next to each other, e.g. [['MEG-retinotopy', 'MEG-freeviewing']] for visually tiring tasks. A name matches the task key itself or its base task, so 'MEG-retinotopy' covers all its options. A task can be in only one group. Defaults to None.
        repeat_pair_min (float, optional): how many minutes of calibration drift the solver accepts, per earlier time, to avoid putting two tasks next to each other again (scaled by how often they would meet by chance). Raise it for more varied orders across sessions; 0 turns it off. Defaults to 1.0.
        spread_weight (float, optional): minutes of calibration drift the solver accepts per minute two blocks of the same task start closer than an even spread through the session. Raise it to push repeats further apart; 0 turns it off. Defaults to 0.25.
        seed (int, optional): Random seed to use. Defaults to 0.
 
    Returns:
        sessions (list[tuple[list[str], int]]): per session, the sequence ("CAL" = calibration,
            "fmap" = fieldmap) and its total duration in seconds.
        remaining (dict[str,int]): blocks left unscheduled after n_sessions, per task.
    """
    rng = random.Random(seed)
    exempt = set(exempt_from_fmap or [])
    has_fmap = lambda a: is_mri and a not in exempt
    dur = {a: d + fmap_duration * has_fmap(a)             # (c) a block includes its fieldmap
           for a, d in tasks_individual_durations.items()}
    n = dict(tasks_repetitions)
    prio = tasks_priorities
    must = mandatory_min_repetitions_per_session
    T, S, c = time_budget_per_session, calib_time_spacing, calib_duration
    K = min(T // S, n_calib_per_session)                   # calibrations, opening included
    B = T - c * K                                          # time left for blocks
    games = [t for t in task_type if task_type[t] == Category.VIDEO_GAME]
    base = min((prio[a] for a in n if a not in games), default=min(prio.values()))
    for a, k in must.items():
        if n[a] < k * n_sessions:
            raise ValueError(f"{a}: {n[a]} repetitions < {k} per session x {n_sessions} sessions.")
    est = math.ceil(sum(n[a] * dur[a] for a in n) / B)
    if est != n_sessions:
        print(f"Note: all repetitions need about {est} sessions; scheduling {n_sessions}.")
    m_ = min(est, n_sessions)
    if len(games) >= 2 and sum(min(n[a], m_) for a in games) < 2 * m_:
        print("Warning: the game queues can't give 2 different games to every session.")
    group_of = {}                                          # keep_apart: task -> its group label
    for i, names in enumerate(keep_apart or []):
        matches = lambda a, g: a == g or a.partition(" -")[0] == g
        unknown = [g for g in names if not any(matches(a, g) for a in n)]
        if unknown:
            print(f"Note: keep_apart names that match no task to schedule: {unknown}")
        for a in n:
            if any(matches(a, g) for g in names):
                if a in group_of:
                    raise ValueError(f"{a} is in more than one keep_apart group.")
                group_of[a] = ("keep_apart", i)
    label = lambda a: group_of.get(a, a)
    apart = [{a for a, g in group_of.items() if g == ("keep_apart", i)}
             for i in range(len(keep_apart or []))]
    # Each game is paced on its own queue: by the end of session s (0-based), game a should
    # have floor(n[a] * (s + 1) / n_sessions + phase[a]) blocks done. Games with the same
    # number of repetitions get evenly spread phases, so they take turns between sessions
    # (e.g. 4 games x 3 repetitions over 6 sessions: 2 of them in every session).
    phase = {}
    for k in sorted({n[a] for a in games}):
        same = sorted(a for a in games if n[a] == k)
        rng.shuffle(same)
        phase.update({a: (i + 0.5) / len(same) for i, a in enumerate(same)})
    game_target = lambda a, s: min(n[a], math.floor(n[a] * (s + 1) / n_sessions + phase[a]))
    used, pairs, firsts, sessions, warned = Counter(), Counter(), Counter(), [], False

    while len(sessions) < n_sessions and any(used[a] < n[a] for a in n):
        blocks = Counter()
        later = n_sessions - len(sessions) - 1                 # sessions after this one
        left = lambda a: n[a] - used[a] - blocks[a] - must.get(a, 0) * later
        free = lambda: B - sum(dur[a] * k for a, k in blocks.items())
        # share[a]: blocks a should get this session. Games: what their own timeline says
        # is due (0 if ahead). Other tasks: remaining blocks spread over the sessions left.
        share = {a: max(must.get(a, 0),
                        max(0, game_target(a, len(sessions)) - used[a]) if a in games
                        else math.ceil((n[a] - used[a]) / (later + 1))) for a in n}
 
        def add(delta):
            trial = blocks + delta
            if all(left(a) >= k for a, k in delta.items()) and orderable(trial, label) \
                    and sum(dur[a] * k for a, k in trial.items()) <= B:
                blocks.update(delta)
                return True
            return False
 
        # 1. Seed: mandatory blocks + 1 block of each of the 2 video games most due on their
        #    own timeline (then least progressed, priority, chance).
        seeds = sorted((a for a in games if left(a) >= 1 and a not in must),
                       key=lambda a: (-share[a], used[a] / n[a], -prio[a], rng.random()))[:2]
        if len(games) >= 2 and len(seeds) < 2 and not warned:
            print(f"Session {len(sessions) + 1}: fewer than 2 games left, "
                  "dropping the >= 2 games rule.")
            warned = True
        if not add(Counter(must) + Counter(seeds)) and not add(Counter(must)):
            raise ValueError("The mandatory blocks don't fit in one session.")
 
        # 2. Fill by fair queueing. Tasks still below their share go first, ordered by
        #    category progress, then how much of their share is already in this session (new
        #    tasks before repeats), then own-queue progress, priority (games lose their edge
        #    once 2 are in), chance. Shares are exceeded only when nothing below its share
        #    fits, and a game repeat its own timeline doesn't call for (each game key counts
        #    as its own game) is tried last of all.
        step = lambda a: 1 if a in blocks or a in games else new_task_blocks
        game_repeat = lambda a: a in games and blocks[a] > 0
        prio_now = lambda a: (base if a in games and sum(g in blocks for g in games) >= 2
                              else prio[a])
 
        def cat_frac(a):  # time progress of a's whole category so far (identical for
            cat = [b for b in n if task_type[b] == task_type[a]]  # tasks of one category,
            done = sum((used[b] + blocks[b]) * dur[b] for b in cat)  # which then compete
            return done / sum(n[b] * dur[b] for b in cat)            # on their own queue)
 
        while free() >= min(dur.values()):
            key = lambda a: (cat_frac(a), blocks[a] / share[a] if share[a] else math.inf,
                             (used[a] + blocks[a] + 1) / n[a],
                             -prio_now(a), rng.random())
            pool = [a for a in n if left(a) > 0]
            tries = []
            for tier in (lambda a: blocks[a] < share[a],                         # due this session
                         lambda a: blocks[a] >= share[a] and not game_repeat(a),  # beyond share
                         lambda a: blocks[a] >= share[a] and game_repeat(a)):     # undue game repeat
                group = sorted(filter(tier, pool), key=key)
                tries += [(a, step(a)) for a in group] + [(a, 1) for a in group if step(a) > 1]
            if not any(add(Counter({a: k})) for a, k in tries):
                break
        if not blocks:
            raise ValueError("A remaining block is longer than the session budget.")
 
        # 3. CP-SAT: order + calibrations (fallback: any valid order, nearest-edge CALs).
        seq = best_order(blocks, dur, time_budget_per_session, task_type, pairs, firsts, n, S, K, rng, apart,
                         repeat_min=repeat_pair_min, calib=c, spread_w=spread_weight) \
            or place_calibrations(quick_order(blocks, label), dur, c, S, K)
        order = [a for a in seq if a != "CAL"]
        total = sum(dur[a] for a in order) + c * seq.count("CAL")
        seq = [x for a in seq for x in ([a, "fmap"] if a in dur and has_fmap(a) else [a])]
        sessions.append((seq, total))
        used += blocks
        firsts[order[0]] += 1
        pairs.update(zip(order, order[1:]))
    remaining = {a: n[a] - used[a] for a in n if used[a] < n[a]}
    if remaining:
        print(f"Unscheduled after {n_sessions} sessions: {remaining}")
    return sessions, remaining