"""Stand-in subtask for trying the scheduler without the real tasks. Runs on Linux, macOS, Windows.

Behaves like a real one: optionally waits for a (simulated) scanner trigger and prints STARTED,
runs for its duration and exits by itself. If asked to stop earlier (SIGTERM, or Ctrl+Break on
Windows) it saves its results and exits cleanly.

    python fake_task.py --task mot --duration 900 --out /tmp/x [--trigger-after 3]
    test switches: --extra S (runs S seconds past its duration), --exit-code N, --ignore-stop
"""

import argparse
import json
import signal
import sys
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--task", required=True)
ap.add_argument("--duration", type=float, required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--trigger-after", type=float, default=None, help="simulate a scanner trigger after S s")
ap.add_argument("--extra", type=float, default=0.0, help="run this many seconds past the duration")
ap.add_argument("--exit-code", type=int, default=0)
ap.add_argument("--ignore-stop", action="store_true", help="ignore stop requests (to test force-kill)")
args, params = ap.parse_known_args()

stopping = False


def on_stop(signum, frame):
    global stopping
    stopping = True


for name in ("SIGTERM", "SIGBREAK"):          # SIGBREAK = Ctrl+Break, Windows only
    if hasattr(signal, name):
        signal.signal(getattr(signal, name), signal.SIG_IGN if args.ignore_stop else on_stop)

print(f"{args.task}: loaded, parameters {params}", flush=True)
t_load = time.monotonic()
if args.trigger_after is not None:
    print(f"{args.task}: waiting for the scanner trigger", flush=True)
    while not stopping and time.monotonic() - t_load < args.trigger_after:
        time.sleep(0.02)
    if not stopping:
        print("STARTED", flush=True)

t_task = time.monotonic()
while not stopping and time.monotonic() - t_task < args.duration + args.extra:
    time.sleep(0.02)                          # a real task would run its trials here

ran = time.monotonic() - t_task
Path(args.out).mkdir(parents=True, exist_ok=True)
(Path(args.out) / "results.json").write_text(json.dumps({"task": args.task, "ran_s": round(ran, 3),
                                                          "stopped": stopping}))
print(f"{args.task}: {'stopped' if stopping else 'done'} after {ran:.2f} s, results saved", flush=True)
sys.exit(args.exit_code)
