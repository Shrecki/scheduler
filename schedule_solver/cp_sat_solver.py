from .utils import get_task_durations_and_budgets,compute_and_print_time_shares, print_schedule, schedule_to_tasks, print_task_sequence
from .rules import schedule
from .task_specs import meg_task_n_repetitions, meg_task_priorities, games_allowed_durations, retained_levels, task_options_meg, tasks_meg_dict
from .task_specs import mri_task_n_repetitions, mri_task_priorities, games_allowed_durations, retained_levels, task_options_mri, tasks_mri_dict
from adapters.fmri_gym import SessionScripts
from model import task_definitions as td
from model.session import Session
from pathlib import Path
from .utils import print_task_sequence, export_task_sequence

if __name__ == "__main__":
    session_budget = 105*60 # 105 minutes per session (1h45)
    n_sessions = 6 # the number of planned sessions to reach our objective
    calibration_time = 2*60 # We allow up to two minutes for a given calibration before giving up and proceeding
    calibration_spacing = 20*60 # We space our calibrations every 20 minutes
    TYPE = "MEG" #
    n_sessions = 6
    
    writeout = True
    writePath = f"/home/guibertf/DBP/DBP_sessionTemplate/{TYPE}/"
    
    if TYPE == "MRI":
        mri_task_target_budget, mri_task_individual_durations, mri_task_types = get_task_durations_and_budgets(retained_levels, task_options_mri, mri_task_priorities, mri_task_n_repetitions, games_allowed_durations,tasks_mri_dict)
    
        compute_and_print_time_shares(session_budget, n_sessions, calibration_time, mri_task_target_budget, mri_task_n_repetitions,mri_task_types)
            
        # Schedule according to all specs the sessions
        sessions, remaining = schedule(mri_task_individual_durations, mri_task_n_repetitions, mri_task_priorities,mri_task_types, n_sessions = n_sessions, time_budget_per_session=session_budget, n_calib_per_session=5, calib_time_spacing = calibration_spacing, mandatory_min_repetitions_per_session={}, is_mri=True, exempt_from_fmap=[])
        print_schedule(sessions, mri_task_individual_durations)    
    elif TYPE == "MEG":
        meg_task_target_budget, meg_task_individual_durations, meg_task_types = get_task_durations_and_budgets(retained_levels, task_options_meg, meg_task_priorities, meg_task_n_repetitions, games_allowed_durations,tasks_meg_dict)

        n_sessions = 6
        compute_and_print_time_shares(session_budget, n_sessions, calibration_time, meg_task_target_budget, meg_task_n_repetitions,meg_task_types)
            
        # Schedule according to all specs the sessions
        sessions, remaining = schedule(meg_task_individual_durations, meg_task_n_repetitions, meg_task_priorities,meg_task_types, n_sessions = n_sessions, time_budget_per_session=session_budget, n_calib_per_session=5, calib_time_spacing = calibration_spacing, mandatory_min_repetitions_per_session={'MEG-eye-movements': 1}, is_mri=False, exempt_from_fmap=[])


        print_schedule(sessions, meg_task_individual_durations)    
        task_sequence = schedule_to_tasks(sessions, tasks_meg_dict, "EyeTrack Calibration (MEG)", games_allowed_durations, )
        
        #export(task_sequence, path, subject="sub-01", ses=1, durations=None)
        #print(len(task_sequence))
        print(writePath)

        if writeout:
            export_task_sequence(task_sequence, writePath, "sub-template")
        #print_task_sequence(task_sequence)
        