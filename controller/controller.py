"""Turns everything that comes from outside (the user, the task processes, the clock) into calls on
the model: state-machine events, or the model's update methods. It never updates the view."""

from PySide6.QtCore import QTimer


class Controller:
    TICK_MS = 200       # clock refresh, and how often the session time is checked

    def __init__(self, sm, view, launcher):
        self.sm, self.session, self.view = sm, sm.model, view
        view.modeToggled.connect(sm.change_mode)
        view.startClicked.connect(sm.start)
        view.skipClicked.connect(sm.skip)
        view.stopClicked.connect(sm.end_session)
        view.blockDropped.connect(self.session.add_block)
        view.blockMoved.connect(self.session.move_block)
        view.blockRemoved.connect(self.session.remove_block)
        view.repeatRequested.connect(self.session.repeat_blocks)
        view.shuffleRequested.connect(self.session.shuffle_blocks)
        view.interleaveRequested.connect(self.session.interleave_blocks)
        view.taskChosen.connect(self.session.set_task)
        view.paramChanged.connect(self.session.set_param)
        view.durationChanged.connect(self.session.set_duration)
        view.loadDesign.connect(self.session.load_design)
        view.saveDesign.connect(self.session.save_design)
        view.exportScript.connect(self.session.save_script)
        view.importScript.connect(self.session.load_script)
        view.templateFolderChosen.connect(self.session.scan_templates)
        view.templateDropped.connect(self.session.add_template)
        view.templateReplaced.connect(self.session.load_template)
        view.autoplayToggled.connect(self.set_autoplay)
        view.playRequested.connect(lambda index: sm.play(index=index))
        launcher.launched.connect(sm.launched)
        launcher.exited.connect(lambda code: sm.process_exited(exit_code=code))
        self.timer = QTimer(interval=self.TICK_MS, timeout=self.tick)

    def run(self) -> bool:
        info = self.view.ask_session(self.session.data_root)
        if info is None:
            return False
        self.session.subject, self.session.session_number = info.subject_name, info.session_number
        self.sm.validate()
        if folder := self.view.template_folder():   # the template library of the last launch
            self.session.scan_templates(folder)
        if info.previous:                       # session 02+: start from the previous session's design
            self.session.load_design(info.previous)
        self.view.show()
        self.timer.start()
        return True

    def set_autoplay(self, on: bool):
        self.session.set_autoplay(on)
        if on:
            self.sm.resume()                    # idle: go on with the next run (ignored otherwise)

    def tick(self):
        if self.session.in_state("recording"):
            if self.session.time_is_up():
                self.sm.end_session()           # ignored once the session is already ending
            self.session.tick()