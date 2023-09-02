"""
This script does the following steps:
- walk through the whole work_dirs directory
- detect and print all model folders that have not yet been inferred (i.e. the prediction csv file is missing)
- prompt the user to start the infer process
- generate the infer commands
- batch all commands on the corresponding gpus, whereas each gpu is dedicated for a specific task

Prediction File Naming Scheme:  TASK_N-shot_submission.csv
Example:                        chest_10-shot_submission.csv
"""
import itertools
import os
import re
import subprocess
import sys
import shutil
import time
from collections import Counter
from multiprocessing import Pool
from functools import lru_cache
from tensorboard.backend.event_processing.event_accumulator import EventAccumulator

EXP_PATTERN = re.compile(r'exp(\d+)')


def run_commands_on_cluster(commands, num_commands, delay_seconds=1):
    """
    Runs the generated commands on the cluster.
    Tasks are allocated to GPUs based on the task type:
    - colon: rtx4090 (gpuc)
    - chest: rtx3090 (gpub)
    - endo: rtx3090 (gpua)
    """

    task_gpu_map = {
        'colon': 'rtx4090',
        'chest': 'rtx3090',
        'endo': 'rtx3090'
    }

    task_counter = {
        'colon': 0,
        'chest': 0,
        'endo': 0
    }

    for command in commands:
        task = command.split("/")[6]

        if task not in task_gpu_map:
            raise ValueError(f'Invalid task {task} in command {command}.')

        # Check if we have already run the desired number of commands for this task
        if task_counter[task] >= num_commands:
            continue

        gpu = task_gpu_map[task]

        cfg_path = command.split(" ")[2]
        cfg_path_split = cfg_path.split("/")
        shot, exp = cfg_path_split[6], extract_exp_number(cfg_path_split[7])

        log_dir = cfg_path.rsplit("/", 1)[0]
        log_file_name = f"{task}_{shot}_exp{exp}_slurm-%j"

        # Ensure the log directory exists
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

        #slurm_cmd = f'sbatch -p ls6 --gres=gpu:{gpu}:1 --wrap="{command}" -o "{log_dir}/{log_file_name}.out"'
        slurm_cmd = f'sbatch -p ls6 --nodelist=gpuc --wrap="{command}" -o "{log_dir}/{log_file_name}.out"'
        print(slurm_cmd)

        # Increment the counter for the task
        task_counter[task] += 1

        subprocess.run(slurm_cmd, shell=True)
        time.sleep(delay_seconds)


def get_file_from_directory(directory, extension, contains_string=None):
    """Get a file from an absolute directory (i.e. from /scratch/..) with the given extension and optional substring."""
    for file in os.listdir(directory):
        if file.endswith(extension) and (not contains_string or contains_string in file):
            return os.path.join(directory, file)
    return None


def print_report(model_infos):
    model_dirs = [model["path"] for model in model_infos.values()]
    sorted_report_entries = sorted([model_dir for model_dir in model_dirs], key=sort_key)
    report = [
        "\n---------------------------------------------------------------------------------------------------------------",
        f"| Valid Models without an existing prediction CSV file:",
        "---------------------------------------------------------------------------------------------------------------",
        *sorted_report_entries,
        "---------------------------------------------------------------------------------------------------------------"
    ]
    for line in report:
        print(line)


def sort_key(entry):
    # Extract task, shot, and experiment number from the entry
    parts = entry.split('/')
    task = parts[5]
    shot = int(parts[6].split('-')[0])
    exp_number = extract_exp_number(parts[-1])
    return task, shot, exp_number


def my_print(message):
    sys.stdout.write(message + '\n')
    sys.stdout.flush()


def process_task_shot_combination(args):
    task, shot = args
    return task, shot, get_model_dirs_without_prediction(task=task, shot=shot)


def extract_exp_number(string):
    match = EXP_PATTERN.search(string)
    return int(match.group(1)) if match else 0


def contains_csv_file(task, shot, model_dir):
    expected_filename = f"{task}_{shot}-shot_submission.csv"

    try:
        return os.path.exists(os.path.join(model_dir, expected_filename))
    except FileNotFoundError:
        pass
    except PermissionError as permission_error:
        my_print(f"Permission Error encountered: {permission_error}")
        return False

    return False


@lru_cache(maxsize=None)
def is_metric_in_event_file(file_path, metric):
    event_acc = EventAccumulator(file_path, size_guidance={'scalars': 0})  # 0 means load none, just check tags
    event_acc.Reload()
    scalar_tags = event_acc.Tags()['scalars']

    return metric in scalar_tags


@lru_cache(maxsize=None)
def get_event_file_from_model_dir(model_dir):
    try:
        for entry in os.listdir(model_dir):
            full_path = os.path.join(model_dir, entry)
            if os.path.isdir(full_path):
                full_path = os.path.join(full_path, "vis_data")
                event_file = os.listdir(full_path)[0]
                return os.path.join(full_path, event_file)
    except Exception:
        return None


def get_model_dirs_without_prediction(task, shot):
    model_dirs = []
    setting_directory = os.path.join(work_dir_path, task, f"{shot}-shot")

    try:
        setting_model_dirs = os.listdir(setting_directory)
    except Exception:
        return None

    for model_dir in setting_model_dirs:
        my_print(f"Checking {task}/{shot}-shot/{model_dir}")
        abs_model_dir = os.path.join(setting_directory, model_dir)

        # Skip if no best checkpoint file
        checkpoint_path = get_file_from_directory(abs_model_dir, ".pth", "best")
        if checkpoint_path is None:
            continue

        # Skip/Delete if no event file
        event_file = get_event_file_from_model_dir(abs_model_dir)
        if event_file is None:
            #print("No event file found, skipping..")
            continue

        # Skip if metric not in event file
        if not is_metric_in_event_file(event_file, metric_tags['map']):
            #print("Metric map not present, skipping..")
            continue

        # Skip if prediction csv file is present
        if contains_csv_file(task, shot, abs_model_dir):
            continue

        model_dirs.append(model_dir)
    return model_dirs


# ========================================================================================
work_dir_path = os.path.join("/scratch", "medfm", "medfm-challenge", "work_dirs")
tasks = ["colon", "endo", "chest"]
shots = ["1", "5", "10"]
N_inferences_per_task = 10
batch_size = 4
metric_tags = {"auc": "AUC/AUC_multiclass",
               "aucl": "AUC/AUC_multilabe",
               "map": "multi-label/mAP",
               "agg": "Aggregate"}
# ========================================================================================


if __name__ == "__main__":  # Important when using multiprocessing
    with Pool() as pool:
        combinations = [(task, shot) for task in tasks for shot in shots]

        # Use imap_unordered and directly iterate over the results
        results = []
        for result in pool.imap_unordered(process_task_shot_combination, combinations):
            results.append(result)

    model_infos = {}
    for task, shot, model_list in results:
        for model_name in model_list:
            model_path = os.path.join(work_dir_path, task, f"{shot}-shot", model_name)
            exp_num = extract_exp_number(model_name)
            model_infos[model_name] = {
                "task": task,
                "shot": shot,
                "exp_num": exp_num,
                "name": model_name,
                "path": model_path,
            }

    print_report(model_infos)

    user_input = input(f"\nDo you want to generate the inference commands? (yes/no): ")
    if user_input.strip().lower() == 'no':
        exit()

    commands = []
    for model in model_infos.values():
        task = model['task']
        shot = model['shot']
        exp_num = model['exp_num']
        model_path = model['path']
        model_name = model['name']

        # Config Path
        config_filepath = get_file_from_directory(model_path, ".py")

        # Checkpoint Path
        checkpoint_filepath = get_file_from_directory(model_path, ".pth", "best")

        # Image Path
        images_path = os.path.join(work_dir_path, "data", "MedFMC_test", task, "images")

        # Destination Path
        out_filepath = os.path.join(model_path, f"{task}_{shot}-shot_submission.csv")

        command = (f"python tools/infer.py {config_filepath} {checkpoint_filepath} {images_path} --batch-size {batch_size} --out {out_filepath}")
        commands.append(command)

    print("Generated Infer Commands:")
    for command in commands:
        print(command)

    task_counts = Counter(model["task"] for model in model_infos.values())

    print("Task Counts:")
    for task, count in task_counts.items():
        print(f"{task.capitalize()}: {count}")

    while True:
        user_input = input("\nHow many inference commands per task do you want to generate? ").strip().lower()

        if user_input == 'no':
            exit()

        try:
            num_commands = int(user_input)
            break
        except ValueError:
            print("Invalid input. Please enter a number or 'no' to exit.")

    run_commands_on_cluster(commands, num_commands)
