# Block scheduler

Plans a session as a timeline of blocks, then runs each block's task as its own process.

    pip install -r requirements.txt
    python main.py

## Installation

```sh
git clone [repo name]
cd [repo name]
uv venv schedulerEnv --python 3.11
uv pip install "PySide6-Essentials==6.11.3" "python-statemachine>=3.2"
```

This environment makes no assumption as to the individual environments available elsewhere!
Activation of environments should be done in the function calls of the relevant programs!

## Example usage

To launch the GUI: 

```sh
python main.py
```

## Setting up the tasks

The tasks available are in model/tasks.py.
A task is defined by a (unique) key, a display name, a default duration (in seconds) and a launch command.
For fmri-gym env and others, please ensure the command includes activation of adequate environments, as the scheduler's  env is not assumed to contain them (to ensure no breaking dependencies across softwares).

## Layout (MVC)

| Folder | File | Role |
|---|---|---|
| `model/` | `statechart.py` | the states and transitions: the whole flow, readable at a glance |
| | `session.py` | the data and rules; `notify()` tells the view to redraw |
| | `tasks.py` | the task catalogue: **add your tasks here** |
| `view/` | `window.py` | the main window: `refresh()` redraws from the session |
| | `blocks.py` | the block palette and the timeline (drag and drop) |
| | `session_dialog.py`, `theme.py` | startup dialog, colours |
| `controller/` | `controller.py` | turns clicks, process exits and the clock into model calls |
| | `launcher.py` | starts and stops one task process (Linux, macOS, Windows) |

Rules: the view only reads the model and emits signals; the controller only calls the model (state
machine events or update methods); the model never imports Qt, and notifies its observers itself.

## Tasks

A task ends itself after its planned `{duration_s}`; the scheduler waits for it. It is only stopped
when the session's time is used up, or by Skip / Stop. A task that waits for the scanner prints its
`start_line` (e.g. `STARTED`) when the trigger arrives; its block's clock starts then.
`fake_task.py` is a stand-in task used by the demo catalogue.
