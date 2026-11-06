"""The states of the scheduler and what may happen in each. Pure Python: no Qt.

The controller only sends events; everything that follows from an event is decided here.
"stop" is an action (ask the task to stop); "process_exited" is the event that moves on, so a new
block never starts before the previous task has really gone.
"""

from statemachine import State, StateChart
 
from .session import Session, now_ms
 
 
class Scheduler(StateChart[Session]):
    catch_errors_as_events = False      # let bugs in callbacks raise instead of being swallowed
 
    initiate = State(initial=True)
    design = State()
    execute = State()
 
    class recording(State.Compound):
        next_block_await = State(initial=True)  # entering it launches the next block's task
        block_running = State()                 # its start line arrived (scanner trigger)
        quit_sequence = State()                 # session over: waiting for the task to exit
 
        launched = next_block_await.to(block_running)
        skip = next_block_await.to(None) | block_running.to(None)     # targetless: action only, no exit/entry
        end_session = next_block_await.to(quit_sequence) | block_running.to(quit_sequence)
 
    finalize = State(final=True)
 
    validate = initiate.to(design)
    change_mode = design.to(execute) | execute.to(design)
    start = execute.to(recording, cond="has_blocks")
    process_exited = (
        recording.block_running.to(recording.next_block_await, cond="can_continue")
        | recording.next_block_await.to.itself(cond="can_continue")    # exited before its trigger
        | recording.block_running.to(finalize)
        | recording.next_block_await.to(finalize)
        | recording.quit_sequence.to(finalize)
    )
 
    def on_enter_recording(self):
        self.model.start_recording()
 
    def on_enter_next_block_await(self):
        self.model.launch_next()
 
    def on_enter_block_running(self):
        self.model.mark_started()
 
    def on_process_exited(self, exit_code=None):
        self.model.mark_exited(exit_code)
 
    def on_skip(self):
        self.model.processes.stop()
 
    def on_end_session(self):
        self.model.processes.stop()
 
    def on_enter_finalize(self):
        self.model.rec_end = now_ms()
 
    def after_transition(self):
        self.model.notify()
