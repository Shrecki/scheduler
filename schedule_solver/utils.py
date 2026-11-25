from dataclasses import dataclass, field
 
from model.tasks import Task, Category
from pathlib import Path
 
from adapters.fmri_gym import SessionScripts
from model import task_definitions as td
from model.session import Session

def parse_key(key: str) -> tuple[str, str]:
    """'MEG-left-right -audio' -> ('MEG-left-right', '-audio');  'MEG-movie' -> ('MEG-movie', '')"""
    base, sep, variant = key.partition(" -")
    return base, ("-" + variant) if sep else ""


def get_task_durations_and_budgets(retained_levels, task_options, task_priorities, task_n_repetitions, games_allowed_durations, task_dict):
    # Keys above are either "<task label>" or "<task label> <level/option>", e.g. "MEG-left-right -audio"
    # Sanity checks: a misspelled level/option used to be silently accepted (and given the base task's duration)
    declared_variants = {**retained_levels, **task_options}
    if set(task_n_repetitions) != set(task_priorities):
        raise ValueError(f"Repetitions and priorities keys differ: {set(task_n_repetitions) ^ set(task_priorities)}")
    for k in task_n_repetitions:
        base, variant = parse_key(k)
        if base not in games_allowed_durations and base not in task_dict:
            raise ValueError(f"'{k}': unknown task '{base}'")
        if variant not in declared_variants.get(base, [""]):
            raise ValueError(f"'{k}': '{variant}' is not a declared level/option of '{base}' (declared: {declared_variants.get(base, 'none')})")

    task_types: dict[str, Category] = {}
    for k in task_n_repetitions:
        base, _ = parse_key(k)
        if base in games_allowed_durations:
            task_types[k] = Category.VIDEO_GAME
        else:
            task_types[k] = task_dict[base].category

    # Defines the individual durations of tasks and games (automatically extracted from task definitions), in seconds
    # NB: all options of a task are assumed to last as long as the base task
    task_individual_durations: dict[str, int] = {}
    for k in task_n_repetitions:
        base, _ = parse_key(k)
        if base in games_allowed_durations:
            task_individual_durations[k] = games_allowed_durations[base]
        else:
            if base == "MEG-WCS":
                task_individual_durations[k] = 8*60
            else:    
                task_individual_durations[k] = int(task_dict[base].duration_s)

    # Defines the projected total duration (number of repetitions * duration per repetition), in seconds
    task_target_budget: dict[str, int] = {
        k: task_individual_durations[k] * n for k, n in task_n_repetitions.items()
    }
    return task_target_budget, task_individual_durations, task_types


def compute_and_print_time_shares(session_budget, n_sessions, calibration_time, task_target_budget, task_n_repetitions,task_types):
    # Define here session constants
    total_budget = n_sessions * session_budget
    n_calibration_ideal = int(session_budget / (20*60))
    total_time_calibration = n_sessions * n_calibration_ideal * calibration_time

    # Define here actual total measured time and measured overbudget (if any)
    total_time_measured = sum(task_target_budget.values())
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
    for k in task_target_budget:
        groups.setdefault(parse_key(k)[0], []).append(k)

    # Build the rows first: (label, n_repetitions, seconds), so columns can be aligned afterwards
    rows: list[tuple[str, int, int]] = []
    for base, keys in groups.items():
        if keys == [base]:  # plain task, no level/option
            rows.append((f"| {base}", task_n_repetitions[base], task_target_budget[base]))
            continue
        rows.append((f"| {base}",
                    sum(task_n_repetitions[k] for k in keys),
                    sum(task_target_budget[k] for k in keys)))
        for i, k in enumerate(keys):
            branch = "`--" if i == len(keys) - 1 else "|--"
            rows.append((f"| {branch} {parse_key(k)[1]}", task_n_repetitions[k], task_target_budget[k]))

    label_width = max(len(label) for label, _, _ in rows) + 2
    for label, reps, secs in rows:
        rep_word = "repetitions" if reps != 1 else "repetition "
        print(f"{label:<{label_width}}{reps:>3} {rep_word} -> {fmt_duration(secs):>8} ({secs / (total_time_measured + total_time_calibration):6.2%} total time)")

    # Category totals (from meg_task_types, rather than guessing from the task name)
    total_game = sum(v for k, v in task_target_budget.items() if task_types[k] == Category.VIDEO_GAME)
    total_localizers = sum(v for k, v in task_target_budget.items() if task_types[k] == Category.LOCALIZER_MEG)
    total_tasks = total_time_measured - total_game - total_localizers

    print("===============")
    print("Totals by category:")
    for name, secs in [("Games", total_game), ("Localizers", total_localizers), ("Tasks", total_tasks)]:
        print(f"| {name:<12}{fmt_duration(secs):>9} ({secs / (total_time_measured+total_time_calibration):6.2%})")
    print(f"| {'Eyetrack cal':<12}{fmt_duration(total_time_calibration):>9} ({total_time_calibration/(total_time_measured+total_time_calibration):6.2%})")
    print(f"| {'All':<12}{fmt_duration(total_time_measured+total_time_calibration):>9}")


def print_schedule(sessions, durations, calib_duration=120, fmap_duration=30):
    """One item per line, with its start time (mm:ss) within the session."""
    length = {"CAL": calib_duration, "fmap": fmap_duration} | durations
    for k, (seq, total) in enumerate(sessions, 1):
        print(f"\nSession {k} ({total // 60} min {total % 60:02d} s)")
        t = 0
        for item in seq:
            print(f"  {t // 60:3d}:{t % 60:02d}  {item}")
            t += length[item]


@dataclass(frozen=True)
class Run:
    """One run of a session, as the scheduler plays it: its task (None: a fieldmap, which is no task),
    the parameters that differ from the task's defaults ({"curriculum": "game1"}), its duration (None:
    the task's own), and the variant of its key ("-game1"; "" if none)."""
    task: Task | None
    params: dict = field(default_factory=dict)
    duration_s: float | None = None
    variant: str = ""

def schedule_to_tasks(sessions, task_dict, calib_label, games_allowed_durations, keep_fieldmaps=False):
    """Turn each session's sequence of keys into Runs of the tasks in task_dict. A game plays the
    curriculum its variant names ("-game1": game1, configs/curricula/<backend>/game1.json; no variant:
    the task's default) for its duration in games_allowed_durations. "CAL" is task_dict[calib_label];
    fieldmaps are dropped, or kept as Run(None, variant="fmap")."""
    if calib_label not in task_dict:
        raise KeyError(f"Calibration task '{calib_label}' is not in task_dict")
    out = []
    for seq, _ in sessions:
        runs = []
        for key in seq:
            if key == "fmap":
                if keep_fieldmaps:
                    runs.append(Run(None, variant="fmap"))
            elif key == "CAL":
                runs.append(Run(task_dict[calib_label]))
            else:
                base, variant = parse_key(key)
                if base not in task_dict:
                    raise KeyError(f"'{base}' (from '{key}') is not in task_dict")
                task = task_dict[base]
                if "curriculum" in task.params:            # a game (GymTask), not its script line
                    curriculum = variant.removeprefix("-")
                    runs.append(Run(task, {"curriculum": curriculum} if curriculum else {},
                                    games_allowed_durations[base], variant))
                else:
                    runs.append(Run(task, variant=variant))
        out.append(runs)
    return out

 
def print_task_sequence(task_sequence):
    for k, runs in enumerate(task_sequence, 1):
        print(f"\nSession {k}")
        for i, run in enumerate(runs, 1):
            if run.task is None:
                print(f"  {i:2d}. fieldmap")
                continue
            line = f"{run.task.label} {run.variant}".strip()
            if "curriculum" in run.task.params:
                line += f"  ({run.duration_s} s, curriculum={({**run.task.defaults(), **run.params})['curriculum']})"
            print(f"  {i:2d}. {line}")
 
 
def export(runs, path, subject="sub-01", ses=1):
    """Runs (fieldmaps left out) as <path>.json and <path>.sh, as Save design… writes them."""
    s = Session(processes=None, scripts=SessionScripts(td.FMRI_GYM_PATH, td.TASKS, paths=td.PATHS))
    s.subject, s.session_number = subject, ses
    for run in runs:
        if run.task is None:
            continue
        if wrong := {k: v for k, v in run.params.items() if not run.task.accepts(k, v)}:
            raise ValueError(f"{run.task.key}: not one of its choices: {wrong}")
        s.blocks.append(block := s.new_block(run.task))
        block.params.update(run.params)
        if run.duration_s is not None:
            block.duration_ms = int(run.duration_s * 1000)
    s.save(path)
    return s.notice
 
 
def export_task_sequence(task_sequences, folder, subject):
    """Each session's runs as a file pair of its own: <folder>/<subject>_ses-NN.json and .sh."""
    for ses, runs in enumerate(task_sequences, 1):
        print(export(runs, Path(folder) / f"{subject}_ses-{ses:02d}", subject, ses))
 