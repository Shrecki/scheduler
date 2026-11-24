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


def place_calibrations(order, dur, calib, every):
    """Insert 'CAL' blocks; returns (sequence, total minutes)."""
    seq, t, due = ["CAL"], calib, calib + every
    for a in order:
        if due <= t + dur[a] / 2:          # due point in this block's first half
            seq.append("CAL")              # (or already passed) -> calibrate before
            t += calib
            due = t + every
        seq.append(a)
        t += dur[a]
    return seq, t


def best_order(blocks, pairs, firsts, n, rng, time_limit=1.0):
    names, N = sorted(+blocks), sum(blocks.values())
    nbar = sum(n.values()) / len(n)
    if N == 1:
        return names
    m = cp_model.CpModel()
    z = {(p, a): m.new_bool_var("") for p in range(N) for a in names}
    cost = []
    for p in range(N):
        m.add_exactly_one(z[p, a] for a in names)
    for a in names:
        m.add(sum(z[p, a] for p in range(N)) == blocks[a])
        for p in range(N - 1):
            m.add_bool_or([~z[p, a], ~z[p + 1, a]])              # no repeat
        off = m.new_int_var(0, N * N, "")                        # off-centre-ness
        m.add_abs_equality(off, sum((N - 1 - 2 * p) * z[p, a] for p in range(N)))
        cost += [10 * off, 10 * firsts[a] * z[0, a]]
    for p in range(N - 1):
        for a in names:
            for b in names:
                # past count relative to chance (big queues meet more often)
                w = round(10 * pairs[a, b] * nbar ** 2 / (n[a] * n[b]))
                if a != b and w:
                    t = m.new_bool_var("")
                    m.add_bool_or([~z[p, a], ~z[p + 1, b], t])
                    cost.append(w * t)
    cost += [rng.randint(0, 9) * v for v in z.values()]          # variety
    m.minimize(sum(cost))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.random_seed = rng.randint(0, 10**6)
    if solver.solve(m) not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return quick_order(blocks)
    return [a for p in range(N) for a in names if solver.value(z[p, a])]


    

def schedule(tasks_individual_durations: dict[str,int], tasks_repetitions: dict[str,int], tasks_priorities: dict[str, int], task_type: dict[str,Category], n_sessions: int=6, time_budget_per_session: int=105*60, n_calib_per_session: int=5, calib_time_spacing:int=20*60, calib_duration: int=2*60, mandatory_min_repetitions_per_session: dict[str,int]={}, is_mri: bool=False, fmap_duration: int=30, exempt_from_fmap: list[str]|None=None, seed=0):
    """
    Given task and session specifications, design a schedule to best satisfy all constraints.
    
    Conceptually, the algorithm maintains queues per each specified task, with associated priorities.
    While scheduling the Nth session, it chooses tasks to include in the session according to a weighted fair queueing scheme.
    From the task selections, it then applies a CP-SAT approach, aiming to satisfy the following rules:
    -
    - 
    -
    If, from the produced schedule, there is still enough time to include more tasks, the algorithm goes back to the queue to add 
    more blocks, and reruns the CP-SAT.

    Args:
        tasks_individual_durations (dict[str,int]): a dictionary mapping each task to be included to a duration in seconds
        tasks_repetitions (dict[str,int]): a dictionary mapping each task to how many times we want the task repeated over all sessions in total
        tasks_priorities (dict[str, int]): a dictionary mapping each task to a priority to be chosen (higher number is higher priority)
        task_type (dict[int, Category]): a dictionary, mapping each task to a task Category type.
        n_sessions (int, optional): Number of sessions to schedule. Defaults to 6.
        time_budget_per_session (int, optional): The desired session length, given in seconds. Defaults to 105*60 seconds.
        n_calib_per_session (int, optional): Number of eyetracker calibrations per session. Defaults to 5.
        calib_time_spacing (int, optional): Ideally, the spacing between two eyetracker calibration episodes, in seconds. Defaults to 20*60.
        calib_duration (int, optional): The duration of an eyetracking episode, in seconds. Defaults to 2*60.
        mandatory_min_repetitions_per_session (dict[str,int], optional): a dictionary, indicating specific tasks where a set minimum number of repetitions for each session is desired. Use it if you want to have e.g. a specific localizer always present in all sessions. The specified min repetition number should therefore be at least as high as the number of repetitions in task_repetitions. Defaults to {}.
        is_mri (bool, optional): a boolean indicating whether the modality is MRI or not. If MRI, after every task not exempt from fieldmaps, a fieldmap block is appended. Defaults to False.
        fmap_duration (int, optional): the duration, in seconds, of a single fieldmap block. Defaults to 30.
        exempt_from_fmap (list[str] | None, optional): a list of task keys for which a fieldmap block should NOT BE added. Defaults to None.
        seed (int, optional): Random seed to use. Defaults to 0.
    """
    rng = random.Random(seed)
    exempt = set(exempt_from_fmap or [])
    has_fmap = lambda a: is_mri and a not in exempt
    dur = {a: d + fmap_duration * has_fmap(a)             # a block includes its fieldmap
           for a, d in tasks_individual_durations.items()}
    games = [t for t in task_type if task_type[t] == Category.VIDEO_GAME]
    n, prio, must = tasks_repetitions, tasks_priorities, mandatory_min_repetitions_per_session
    budget, every, calib = time_budget_per_session, calib_time_spacing, calib_duration
    top = [a for a in n if prio[a] == max(prio.values())]   # e.g. the video games
    base = min(prio.values())
    for a, k in must.items():
        if n[a] < k * n_sessions:
            raise ValueError(f"{a}: {n[a]} repetitions < {k} per session x {n_sessions} sessions.")
    est = math.ceil(sum(n[a] * dur[a] for a in n) / (budget - n_calib_per_session * calib))
    if est != n_sessions:
        print(f"Note: all repetitions need about {est} sessions; scheduling {n_sessions}.")
    if len(top) >= 2 and sum(n[a] // 2 for a in top) // 2 < min(est, n_sessions):
        print("Warning: the top-priority queues can't give 2 tasks x 2 blocks to every session.")
    used, pairs, firsts, sessions, warned = Counter(), Counter(), Counter(), [], False
 
    def time_of(blocks):
        if not orderable(blocks):
            return math.inf
        return place_calibrations(quick_order(blocks), dur, calib, every, n_calib_per_session)[1]
 
    while len(sessions) < n_sessions and any(used[a] < n[a] for a in n):
        blocks = Counter()
        later = n_sessions - len(sessions) - 1                 # sessions after this one
        left = lambda a: n[a] - used[a] - blocks[a] - must.get(a, 0) * later
        fair = lambda pool: sorted((a for a in pool if left(a) >= 2 and a not in must),
                                   key=lambda a: ((used[a] + blocks[a]) / n[a], -prio[a], rng.random()))
 
        def add(delta):
            if all(left(a) >= k for a, k in delta.items()) \
                    and time_of(blocks + delta) <= budget:
                blocks.update(delta)
                return True
            return False
 
        # 1. Seed: mandatory blocks + 2 blocks of each of the 2 fairest top-priority
        #    tasks, added together (a lone pair A A can't be ordered).
        seeds = fair(top)[:2]
        if len(top) >= 2 and len(seeds) < 2 and not warned:
            print(f"Session {len(sessions) + 1}: top-priority tasks ran out, "
                  "dropping the >= 2 rule.")
            warned = True
        seeds += [a for a in fair(n) if a not in seeds][:2 - len(seeds)]
        if not add(Counter(must) + Counter({a: 2 for a in seeds})) and not add(Counter(must)):
            raise ValueError("The mandatory blocks don't fit in one session.")
 
        # 2. Fill by fair queueing: category progress, then own-queue progress, then
        #    priority (top tasks lose their edge once 2 are in), then chance.
        step = lambda a: 1 if a in blocks else 2
        prio_now = lambda a: (base if a in top and sum(b in blocks for b in top) >= 2
                              else prio[a])
 
        def cat_frac(a):  # progress of a's priority level after adding its step
            cat = [b for b in n if prio[b] == prio[a]]
            done = sum((used[b] + blocks[b]) * dur[b] for b in cat) + step(a) * dur[a]
            return done / sum(n[b] * dur[b] for b in cat)
 
        while budget - time_of(blocks) >= min(dur.values()):
            cands = sorted((a for a in n if left(a) > 0), key=lambda a: (
                cat_frac(a), (used[a] + blocks[a] + step(a)) / n[a],
                -prio_now(a), rng.random()))
            if not any(add(Counter({a: step(a)})) for a in cands) and \
                    not any(add(Counter({a: 1})) for a in cands if a not in blocks):
                break
        if not blocks:
            raise ValueError("A remaining block is longer than the session budget.")
 
        # 3. Order with CP-SAT, then place calibrations and fieldmaps.
        order = best_order(blocks, pairs, firsts, n, rng)
        seq, total = place_calibrations(order, dur, calib, every, n_calib_per_session)
        if total > budget:                 # rare: this order needs one extra
            order = quick_order(blocks)    # calibration; fall back to the order
            seq, total = place_calibrations(order, dur, calib, every,
                                            n_calib_per_session)  # checked above
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
    
    #sessions, remaining = schedule(meg_task_individual_durations, meg_task_n_repetitions, meg_task_priorities,meg_task_types, n_sessions = n_sessions, time_budget_per_session=session_budget, n_calib_per_session=5, calib_time_spacing = 20, mandatory_min_repetitions_per_session={'MEG-eye-movements': 1}, is_mri=False, exempt_from_fmap=[])
    
    # Lastly, represent it as a session schedule (one file per session)