"""A template library: the session designs (.json) and session scripts (.sh) in a folder and its
subfolders, each a ready-made sequence of runs to add to a timeline, or to start one from."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .task_definitions import get_task


@dataclass(frozen=True)
class Template:
    name: str               # its file name, without the extension
    group: str              # its subfolder, from the library's folder ("" for the folder itself)
    path: Path
    design: dict            # as Session.load_design takes it: {"runs": [{"task", "duration_s", "parameters"}...]}
    parts: tuple            # its runs, for showing: (category name, task label, duration in s)

    @property
    def total_s(self) -> float:
        return sum(seconds for _, _, seconds in self.parts)


def find_templates(folder: Path | str, scripts=None) -> tuple[list[Template], list[str]]:
    """The templates under `folder`, by subfolder then name; and the files that look like one but cannot
    be read (a run of a task that task_definitions.py does not have...). A JSON file whose runs have no
    task (a sidecar, a rig, results) is no template; nor is a .sh file that is no session script of
    `scripts`' format. Hidden folders (.git, .venv) are not looked into."""
    folder = Path(folder).expanduser()
    templates, unreadable = [], []
    for path in sorted(_files(folder), key=lambda p: (p.parent != folder, str(p.parent).lower(), p.name.lower())):
        try:
            text = path.read_text(encoding="utf-8")
            if path.suffix == ".json":
                design = json.loads(text)
                runs = design.get("runs") if isinstance(design, dict) else None
                if not isinstance(runs, list) or not all(isinstance(run, dict) and "task" in run for run in runs):
                    continue                                    # some other JSON
            elif scripts is not None and scripts.is_session(text):
                design, _ = scripts.read(text)
            else:
                continue
            parts = tuple((get_task(run["task"]).category.name, get_task(run["task"]).label, float(run["duration_s"]))
                          for run in design["runs"])
        except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError):
            unreadable.append(str(path.relative_to(folder)))
            continue
        if parts:
            group = "" if path.parent == folder else str(path.parent.relative_to(folder))
            templates.append(Template(path.stem, group, path, design, parts))
    return templates, unreadable


def _files(folder: Path):
    """The .json and .sh files under `folder`, hidden folders and files left out."""
    for root, folders, names in os.walk(folder):
        folders[:] = [name for name in folders if not name.startswith(".")]
        yield from (Path(root, name) for name in names if name.endswith((".json", ".sh")) and not name.startswith("."))