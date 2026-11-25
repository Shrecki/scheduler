import math
import random
from collections import Counter
from dataclasses import dataclass
import numpy as np

from ortools.sat.python import cp_model
from .task_definitions import TASKS
from .tasks import Category


#############
# RULES
#############

def orderable(blocks):
    """No task twice in a row is possible iff the commonest task has <= ceil(N/2)."""
    n = sum(blocks.values())
    return n == 0 or max(blocks.values()) <= (n + 1) // 2
 
 
def quick_order(blocks):
    """Any valid order: repeatedly take the commonest task that isn't the last one."""
    left, out = Counter(blocks), []
    while +left:
        a = max((a for a in +left if not out or a != out[-1]), key=left.get)
        out.append(a)
        left[a] -= 1
    return out
 
 
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
 
 
def best_order(blocks, dur, session_budget, task_type, pairs, firsts, n, S, K, rng, pair_min=2, time_limit=2.0):
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
 
    m.add(L[0] == D[0])                                          # (d) window length
    for p in P[1:]:
        m.add(L[p] == D[p]).only_enforce_if(e[p])
        m.add(L[p] == L[p - 1] + D[p]).only_enforce_if(~e[p])
    for p in P[:-1]:                                             # (d) |window - S|
        m.add(dev[p] >= L[p] - S).only_enforce_if(e[p + 1])      #     when it closes
        m.add(dev[p] >= S - L[p]).only_enforce_if(e[p + 1])
    m.add(dev[-1] >= L[-1] - S)                                  # last: only too long
    primary += dev
 
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
    for p in P[:-1]:
        for a in names:
            for b in names:
                w = round(10 * pairs[a, b] * nbar ** 2 / (n[a] * n[b]))  # vs chance
                if a != b and w:
                    t = m.new_bool_var("")
                    m.add_bool_or([~z[p, a], ~z[p + 1, b], t])
                    variety.append(w * t)
    variety += [rng.randint(0, 9) * v for v in z.values()]
 
    m.minimize(session_budget * sum(primary) + sum(variety))
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
 
 
def schedule(tasks_individual_durations: dict[str,int], tasks_repetitions: dict[str,int], tasks_priorities: dict[str, int], task_type: dict[str,Category], n_sessions: int=6, time_budget_per_session: int=105*60, n_calib_per_session: int=5, calib_time_spacing:int=20*60, calib_duration: int=2*60, mandatory_min_repetitions_per_session: dict[str,int]={}, is_mri: bool=False, fmap_duration: int=30, exempt_from_fmap: list[str]|None=None, seed=0):
    """
    Given task and session specifications, design a schedule to best satisfy all constraints.
 
    Conceptually, the algorithm maintains queues per each specified task, with associated priorities.
    Each session reserves time for K = min(floor(T / calib_time_spacing), n_calib_per_session)
    calibrations (the opening one included), leaving B = T - K * calib_duration for blocks.
    Block durations include their fieldmap when is_mri.
    While scheduling the Nth session, it chooses blocks according to a weighted fair queueing scheme:
    - seed: the mandatory blocks, plus 2 blocks of each of the 2 fairest video games
    - fill: repeatedly serve the task whose category (task_type), then whose own queue, is furthest
      behind in time, counting the blocks being added (a new task enters with 2 blocks, a present
      one gets 1; a new task gets 1 block only when nothing else fits). Once 2 video games are in
      the session, games lose their priority edge. A block is accepted only if the blocks still
      fit B and can still be ordered with no task twice in a row.
    Once the blocks are chosen, CP-SAT orders them and places the K - 1 remaining calibrations:
    - hard: every block placed exactly once, one block per position (no overlap, no gap)
    - hard: no task twice in a row (calibrations don't break a repeat)
    - hard: at most K - 1 calibrations besides the opening one (so the session fits T)
    - soft: each calibration window (end of one calibration to start of the next) close to
      calib_time_spacing; the last window only penalised if too long
    - soft: no two neighbouring blocks of the same category, video games exempt (calibrations
      don't separate them)
    - soft, weaker: each task's blocks centred in the session, openers rotated, transitions that
      were frequent in earlier sessions avoided, a small random term for variety
    Every session opens with a calibration.
 
    Args:
        tasks_individual_durations (dict[str,int]): a dictionary mapping each task to be included to a duration in seconds
        tasks_repetitions (dict[str,int]): a dictionary mapping each task to how many times we want the task repeated over all sessions in total
        tasks_priorities (dict[str, int]): a dictionary mapping each task to a priority to be chosen (higher number is higher priority). Only breaks ties in the fair queueing.
        task_type (dict[str, Category]): a dictionary, mapping each task to a task Category type. Categories are balanced against each other in every session; Category.VIDEO_GAME tasks get the 2-games seed.
        n_sessions (int, optional): Number of sessions to schedule. Defaults to 6.
        time_budget_per_session (int, optional): The desired session length, given in seconds. Defaults to 105*60 seconds.
        n_calib_per_session (int, optional): Maximum number of eyetracker calibrations per session, the opening one included. Defaults to 5.
        calib_time_spacing (int, optional): Ideally, the time from the end of one eyetracker calibration to the start of the next, in seconds. Defaults to 20*60.
        calib_duration (int, optional): The duration of an eyetracking episode, in seconds. Defaults to 2*60.
        mandatory_min_repetitions_per_session (dict[str,int], optional): a dictionary, indicating specific tasks where a set minimum number of repetitions for each session is desired. Use it if you want to have e.g. a specific localizer always present in all sessions. The task's total in tasks_repetitions must then be at least this minimum times n_sessions. Defaults to {}.
        is_mri (bool, optional): a boolean indicating whether the modality is MRI or not. If MRI, after every task not exempt from fieldmaps, a fieldmap block is appended. Defaults to False.
        fmap_duration (int, optional): the duration, in seconds, of a single fieldmap block. Defaults to 30.
        exempt_from_fmap (list[str] | None, optional): a list of task keys for which a fieldmap block should NOT BE added. Defaults to None.
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
    if len(games) >= 2 and sum(n[a] // 2 for a in games) // 2 < min(est, n_sessions):
        print("Warning: the game queues can't give 2 games x 2 blocks to every session.")
    used, pairs, firsts, sessions, warned = Counter(), Counter(), Counter(), [], False
 
    while len(sessions) < n_sessions and any(used[a] < n[a] for a in n):
        blocks = Counter()
        later = n_sessions - len(sessions) - 1                 # sessions after this one
        left = lambda a: n[a] - used[a] - blocks[a] - must.get(a, 0) * later
        free = lambda: B - sum(dur[a] * k for a, k in blocks.items())
        fair = lambda pool: sorted((a for a in pool if left(a) >= 2 and a not in must),
                                   key=lambda a: ((used[a] + blocks[a]) / n[a], -prio[a], rng.random()))
 
        def add(delta):
            trial = blocks + delta
            if all(left(a) >= k for a, k in delta.items()) and orderable(trial) \
                    and sum(dur[a] * k for a, k in trial.items()) <= B:
                blocks.update(delta)
                return True
            return False
 
        # 1. Seed: mandatory blocks + 2 blocks of each of the 2 fairest video games,
        #    added together (a lone pair A A can't be ordered).
        seeds = fair(games)[:2]
        if len(games) >= 2 and len(seeds) < 2 and not warned:
            print(f"Session {len(sessions) + 1}: ran out of games, "
                  "dropping the >= 2 games rule.")
            warned = True
        seeds += [a for a in fair(n) if a not in seeds][:2 - len(seeds)]
        if not add(Counter(must) + Counter({a: 2 for a in seeds})) and not add(Counter(must)):
            raise ValueError("The mandatory blocks don't fit in one session.")
 
        # 2. Fill by fair queueing: category progress, then own-queue progress, then
        #    priority (games lose their edge once 2 are in), then chance.
        step = lambda a: 1 if a in blocks else 2
        prio_now = lambda a: (base if a in games and sum(g in blocks for g in games) >= 2
                              else prio[a])
 
        def cat_frac(a):  # time progress of a's whole category so far (identical for
            cat = [b for b in n if task_type[b] == task_type[a]]  # tasks of one category,
            done = sum((used[b] + blocks[b]) * dur[b] for b in cat)  # which then compete
            return done / sum(n[b] * dur[b] for b in cat)            # on their own queue)
 
        while free() >= min(dur.values()):
            cands = sorted((a for a in n if left(a) > 0), key=lambda a: (
                cat_frac(a), (used[a] + blocks[a] + step(a)) / n[a],
                -prio_now(a), rng.random()))
            if not any(add(Counter({a: step(a)})) for a in cands) and \
                    not any(add(Counter({a: 1})) for a in cands if a not in blocks):
                break
        if not blocks:
            raise ValueError("A remaining block is longer than the session budget.")
 
        # 3. CP-SAT: order + calibrations (fallback: any valid order, nearest-edge CALs).
        seq = best_order(blocks, dur, time_budget_per_session, task_type, pairs, firsts, n, S, K, rng) \
            or place_calibrations(quick_order(blocks), dur, c, S, K)
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

if __name__ == "__main__":
    tasks_target_times = {}

    tasks_meg = [t for t in TASKS if t.category in [Category.COGNITIVE_TASK_MEG, Category.EYETRACKER_CALIBRATION,Category.LOCALIZER_MEG,Category.VIDEO_GAME]]
    tasks_mri = [t for t in TASKS if t.category in [Category.COGNITIVE_TASK_MRI, Category.EYETRACKER_CALIBRATION,Category.LOCALIZER_MRI,Category.VIDEO_GAME]]
    
    print([t.label for t in tasks_meg])
    tasks_meg_dict = {t.label: t for t in tasks_meg}
    tasks_mri_dict = {t.label: t for t in tasks_mri}


    # Here define tasks of interest
    retained_meg_tasks = ['AI GameStore', 'Baba Is Auto', 'Rush Hour', 'MEG-left-right', 'MEG-button-press', 'MEG-theory-of-mind', 'MEG-visual-search', 'MEG-verbal-working-memory', 'MEG-Reading-Sentences', 'MEG-eye-movements', 'MEG-Mental-Rotation', 'MEG-Intuitive-Physics', 'MEG-movie', 'MEG-ARC', 'MEG-VCS', 'MEG-WCS','MEG-retinotopy', 'EyeTrack Calibration (MEG)']

    # Specify here the levels/curricula of interest so we can get them all later for scheduling purposes!
    retained_levels: dict[str, list[str]] = {
        'AI GameStore': ['-game1', '-game4', '-game7', '-game8'],
        'Rush Hour': ['-all'],
        'Baba Is Auto': ['-all']
    }

    task_options: dict[str,list[str]] = {
        'MEG-left-right': ['-(defaults)', '-audio'],
        'MEG-retinotopy': ['-(defaults)', '-audio=false -arrows=false']
    }

    # Games do not have an intrinsic duration, so specify here how long each game should be played (in seconds)
    games_allowed_durations: dict[str, int] = {
        'AI GameStore': 600, # Time is measured in seconds here
        'Rush Hour': 600,
        'Baba Is Auto': 900
    }

    # Specify how many repetitions of each game/task is desired
    meg_task_n_repetitions : dict[str, int] = {
        # Games
        'AI GameStore -game1': 3,
        'AI GameStore -game4': 3,
        'AI GameStore -game7': 3,
        'AI GameStore -game8': 3,
        'Baba Is Auto -all': 6,
        'Rush Hour -all': 9,

        # Localizers
        'MEG-left-right -(defaults)': 8,
        'MEG-left-right -audio': 8,
        'MEG-button-press': 8,
        'MEG-theory-of-mind': 1,
        'MEG-visual-search': 1,
        'MEG-verbal-working-memory': 1,
        'MEG-Reading-Sentences': 1,
        'MEG-eye-movements': 10,
        'MEG-Mental-Rotation': 1,
        'MEG-Intuitive-Physics': 1,
        'MEG-movie': 1,

        # Tasks
        'MEG-ARC': 1,
        'MEG-VCS': 1,
        'MEG-WCS': 1,
        'MEG-retinotopy -(defaults)': 8,
        'MEG-retinotopy -audio=false -arrows=false': 6,
    }

    # Specify here the scheduling priority of each game/task (relative to each other)
    meg_task_priorities : dict[str, int] = {
        # Games
        'AI GameStore -game1': 2,
        'AI GameStore -game4': 2,
        'AI GameStore -game7': 2,
        'AI GameStore -game8': 2,
        'Baba Is Auto -all': 2,
        'Rush Hour -all': 2,

        # Localizers
        'MEG-left-right -(defaults)': 1,
        'MEG-left-right -audio': 1,
        'MEG-button-press': 1,
        'MEG-theory-of-mind': 1,
        'MEG-visual-search': 1,
        'MEG-verbal-working-memory': 1,
        'MEG-Reading-Sentences': 1,
        'MEG-eye-movements': 1,
        'MEG-Mental-Rotation': 1,
        'MEG-Intuitive-Physics': 1,
        'MEG-movie': 1,

        # Tasks
        'MEG-ARC': 1,
        'MEG-VCS': 1,
        'MEG-WCS': 1,
        'MEG-retinotopy -(defaults)': 1,
        'MEG-retinotopy -audio=false -arrows=false': 1,
    }

    # Keys above are either "<task label>" or "<task label> <level/option>", e.g. "MEG-left-right -audio"
    def parse_key(key: str) -> tuple[str, str]:
        """'MEG-left-right -audio' -> ('MEG-left-right', '-audio');  'MEG-movie' -> ('MEG-movie', '')"""
        base, sep, variant = key.partition(" -")
        return base, ("-" + variant) if sep else ""

    # Sanity checks: a misspelled level/option used to be silently accepted (and given the base task's duration)
    declared_variants = {**retained_levels, **task_options}
    if set(meg_task_n_repetitions) != set(meg_task_priorities):
        raise ValueError(f"Repetitions and priorities keys differ: {set(meg_task_n_repetitions) ^ set(meg_task_priorities)}")
    for k in meg_task_n_repetitions:
        base, variant = parse_key(k)
        if base not in games_allowed_durations and base not in tasks_meg_dict:
            raise ValueError(f"'{k}': unknown task '{base}'")
        if variant not in declared_variants.get(base, [""]):
            raise ValueError(f"'{k}': '{variant}' is not a declared level/option of '{base}' (declared: {declared_variants.get(base, 'none')})")

    meg_task_types: dict[str, Category] = {}
    for k in meg_task_n_repetitions:
        base, _ = parse_key(k)
        if base in games_allowed_durations:
            meg_task_types[k] = Category.VIDEO_GAME
        else:
            meg_task_types[k] = tasks_meg_dict[base].category

    # Defines the individual durations of tasks and games (automatically extracted from task definitions), in seconds
    # NB: all options of a task are assumed to last as long as the base task
    meg_task_individual_durations: dict[str, int] = {}
    for k in meg_task_n_repetitions:
        base, _ = parse_key(k)
        if base in games_allowed_durations:
            meg_task_individual_durations[k] = games_allowed_durations[base]
        else:
            if base == "MEG-WCS":
                meg_task_individual_durations[k] = 8*60
            else:    
                meg_task_individual_durations[k] = int(tasks_meg_dict[base].duration_s)

    # Defines the projected total duration (number of repetitions * duration per repetition), in seconds
    meg_task_target_budget: dict[str, int] = {
        k: meg_task_individual_durations[k] * n for k, n in meg_task_n_repetitions.items()
    }

    # Define here session constants
    session_budget = 105*60 # 105 minutes per session (1h45)
    n_sessions = 6 # the number of planned sessions to reach our objective
    total_budget = n_sessions * session_budget
    n_calibration_ideal = int(session_budget / (20*60))
    calibration_time = 2*60 # We allow up to two minutes for a given calibration before giving up and proceeding
    total_time_calibration = n_sessions * n_calibration_ideal * calibration_time

    # Define here actual total measured time and measured overbudget (if any)
    total_time_measured = sum(meg_task_target_budget.values())
    overbudget = (total_time_measured + total_time_calibration) - total_budget

    def fmt_duration(seconds: int) -> str:
        minutes, secs = divmod(int(seconds), 60)
        return f"{minutes}m{secs:02d}s"

    # Print as a report
    print("===============")
    print("MEG TIME PLANNING")
    print("===============")
    print(f"Time planning: TARGET SESSION DURATION - {session_budget/60:.0f}min | {n_sessions} SESSIONS REQUIRED | TOTAL SCHEDULED TIME = {total_budget/3600:.2f} hours")
    print("===============")
    print(f"EFFECTIVE SESSION DURATION - {(total_time_measured + total_time_calibration)/(n_sessions*60):.1f} min | {total_time_measured/(n_sessions*60):.1f} min of task - {total_time_calibration/(n_sessions*60):.1f} min of eyetrack calibration")
    if overbudget > 0:
        print(f"We need (assuming we need all {calibration_time/60:.0f} minutes of eyetracker every time!) {overbudget/(n_sessions*60):.1f} extra minutes per session.")
    else:
        print(f"Within budget: {-overbudget/(n_sessions*60):.1f} spare minutes per session.")

    print("===============")
    print("Task by task breakdown:")

    # Group levels/options under their parent task (in order of first appearance, even if not contiguous)
    groups: dict[str, list[str]] = {}
    for k in meg_task_target_budget:
        groups.setdefault(parse_key(k)[0], []).append(k)

    # Build the rows first: (label, n_repetitions, seconds), so columns can be aligned afterwards
    rows: list[tuple[str, int, int]] = []
    for base, keys in groups.items():
        if keys == [base]:  # plain task, no level/option
            rows.append((f"| {base}", meg_task_n_repetitions[base], meg_task_target_budget[base]))
            continue
        rows.append((f"| {base}",
                     sum(meg_task_n_repetitions[k] for k in keys),
                     sum(meg_task_target_budget[k] for k in keys)))
        for i, k in enumerate(keys):
            branch = "`--" if i == len(keys) - 1 else "|--"
            rows.append((f"| {branch} {parse_key(k)[1]}", meg_task_n_repetitions[k], meg_task_target_budget[k]))

    label_width = max(len(label) for label, _, _ in rows) + 2
    for label, reps, secs in rows:
        rep_word = "repetitions" if reps != 1 else "repetition "
        print(f"{label:<{label_width}}{reps:>3} {rep_word} -> {fmt_duration(secs):>8} ({secs / (total_time_measured + total_time_calibration):6.2%} total time)")

    # Category totals (from meg_task_types, rather than guessing from the task name)
    total_game = sum(v for k, v in meg_task_target_budget.items() if meg_task_types[k] == Category.VIDEO_GAME)
    total_localizers = sum(v for k, v in meg_task_target_budget.items() if meg_task_types[k] == Category.LOCALIZER_MEG)
    total_tasks = total_time_measured - total_game - total_localizers

    print("===============")
    print("Totals by category:")
    for name, secs in [("Games", total_game), ("Localizers", total_localizers), ("Tasks", total_tasks)]:
        print(f"| {name:<12}{fmt_duration(secs):>9} ({secs / (total_time_measured+total_time_calibration):6.2%})")
    print(f"| {'Eyetrack cal':<12}{fmt_duration(total_time_calibration):>9} ({total_time_calibration/(total_time_measured+total_time_calibration):6.2%})")
    print(f"| {'All':<12}{fmt_duration(total_time_measured+total_time_calibration):>9}")
    
    # Schedule according to all specs the sessions
    
    sessions, remaining = schedule(meg_task_individual_durations, meg_task_n_repetitions, meg_task_priorities,meg_task_types, n_sessions = n_sessions, time_budget_per_session=session_budget, n_calib_per_session=5, calib_time_spacing = 20, mandatory_min_repetitions_per_session={'MEG-eye-movements': 1}, is_mri=False, exempt_from_fmap=[])
    
    def print_schedule(sessions, durations, calib_duration=120, fmap_duration=30):
        """One item per line, with its start time (mm:ss) within the session."""
        length = {"CAL": calib_duration, "fmap": fmap_duration} | durations
        for k, (seq, total) in enumerate(sessions, 1):
            print(f"\nSession {k} ({total // 60} min {total % 60:02d} s)")
            t = 0
            for item in seq:
                print(f"  {t // 60:3d}:{t % 60:02d}  {item}")
                t += length[item]

    print_schedule(sessions, meg_task_individual_durations)    
    # Lastly, represent it as a session schedule (one file per session)