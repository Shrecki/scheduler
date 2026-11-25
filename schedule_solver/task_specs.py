from model.task_definitions import TASKS
from model.tasks import Category


tasks_target_times = {}

tasks_meg = [t for t in TASKS if t.category in [Category.COGNITIVE_TASK_MEG, Category.EYETRACKER_CALIBRATION,Category.LOCALIZER_MEG,Category.VIDEO_GAME]]
tasks_mri = [t for t in TASKS if t.category in [Category.COGNITIVE_TASK_MRI, Category.EYETRACKER_CALIBRATION,Category.LOCALIZER_MRI,Category.VIDEO_GAME]]

tasks_meg_dict = {t.label: t for t in tasks_meg}
tasks_mri_dict = {t.label: t for t in tasks_mri}


# Here define tasks of interest
retained_meg_tasks = ['AI GameStore', 'Baba Is Auto', 'Rush Hour', 'MEG-left-right', 'MEG-button-press', 'MEG-theory-of-mind', 'MEG-visual-search', 'MEG-verbal-working-memory', 'MEG-Reading-Sentences', 'MEG-eye-movements', 'MEG-Mental-Rotation', 'MEG-Intuitive-Physics', 'MEG-movie', 'MEG-ARC', 'MEG-VCS', 'MEG-WCS','MEG-retinotopy', 'EyeTrack Calibration (MEG)', 'Multi-Task Battery: set_a', 'MEG-freeviewing']

retained_mri_tasks = [['ARC', 'VCST', 'WCST', 'Multi-Task Battery: set_a', 'Retinotopy (MRI)', 'AI GameStore','Baba Is Auto', 'Rush Hour', 'EyeTrack Calibration (MRI)']
]

# Specify here the levels/curricula of interest so we can get them all later for scheduling purposes!
retained_levels: dict[str, list[str]] = {
    'AI GameStore': ['-game1', '-game4', '-game7', '-game8'],
    'Rush Hour': ['-all'],
    'Baba Is Auto': ['-all']
}

task_options_meg: dict[str,list[str]] = {
    'MEG-left-right': ['-(defaults)', '-audio'],
    'MEG-retinotopy': ['-(defaults)', '-audio=false -arrows=false']
}

task_options_mri: dict[str,list[str]] = {}

# Games do not have an intrinsic duration, so specify here how long each game should be played (in seconds)
games_allowed_durations: dict[str, int] = {
    'AI GameStore': 600, # Time is measured in seconds here
    'Rush Hour': 600,
    'Baba Is Auto': 900
}


retained_mri_tasks = [['ARC', 'VCST', 'WCST', 'Multi-Task Battery: set_a', 'Retinotopy (MRI)', 'AI GameStore','Baba Is Auto', 'Rush Hour', 'EyeTrack Calibration (MRI)', 'Syllogism']
]


# Specify how many repetitions of each game/task is desired
mri_task_n_repetitions : dict[str, int] = {
    # Games
    'AI GameStore -game1': 3,
    'AI GameStore -game4': 3,
    'AI GameStore -game7': 3,
    'AI GameStore -game8': 3,
    'Baba Is Auto -all': 6,
    'Rush Hour -all': 9,

    # Localizers
    'Retinotopy (MRI)': 6,
    'Multi-Task Battery: set_a': 8,
    # Tasks
    'ARC': 5,
    'WCST': 7,
    'VCST': 0,
    'Syllogism': 5
}

# Specify here the scheduling priority of each game/task (relative to each other)
mri_task_priorities : dict[str, int] = {
    # Games
    'AI GameStore -game1': 2,
    'AI GameStore -game4': 2,
    'AI GameStore -game7': 2,
    'AI GameStore -game8': 2,
    'Baba Is Auto -all': 2,
    'Rush Hour -all': 2,

    # Localizers
    'Retinotopy (MRI)': 1,
    'Multi-Task Battery: set_a': 1,
    # Tasks
    'ARC': 1,
    'WCST': 1,
    'VCST': 0,
    'Syllogism': 1
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
    'MEG-left-right -(defaults)': 0,
    'MEG-left-right -audio': 0,#8,
    'MEG-button-press': 6,
    'Multi-Task Battery: set_a': 6,
    'MEG-freeviewing': 4,
    #'MEG-theory-of-mind': 1,
    #'MEG-visual-search': 1,
    #'MEG-verbal-working-memory': 1,
    #'MEG-Reading-Sentences': 1,
    'MEG-eye-movements': 10,
    #'MEG-Mental-Rotation': 1,
    #'MEG-Intuitive-Physics': 1,
    #'MEG-movie': 1,

    # Tasks
    'MEG-ARC': 3,
    'MEG-VCS': 3,
    'MEG-WCS': 3,
    'MEG-retinotopy -(defaults)': 6,
    'MEG-retinotopy -audio=false -arrows=false': 0,
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
    'MEG-freeviewing': 1,
    #'MEG-theory-of-mind': 1,
    #'MEG-visual-search': 1,
    #'MEG-verbal-working-memory': 1,
    #'MEG-Reading-Sentences': 1,
    'MEG-eye-movements': 1,
    #'MEG-Mental-Rotation': 1,
    #'MEG-Intuitive-Physics': 1,
    #'MEG-movie': 1,
    'Multi-Task Battery: set_a': 1,

    # Tasks
    'MEG-ARC': 1,
    'MEG-VCS': 1,
    'MEG-WCS': 1,
    'MEG-retinotopy -(defaults)': 1,
    'MEG-retinotopy -audio=false -arrows=false': 1,
}