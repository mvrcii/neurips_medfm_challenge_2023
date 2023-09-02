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
import os
import re
import sys
import shutil
from multiprocessing import Pool
from functools import lru_cache
from tensorboard.backend.event_processing.event_accumulator import EventAccumulator

EXP_PATTERN = re.compile(r'exp(\d+)')


def find_all_csv_files_in_directory(directory):
    return [os.path.join(root, file)
            for root, dirs, files in os.walk(directory)
            for file in files if file.endswith('.csv')]


def matches_model_directory(csv_file, task, shot):
    csv_name = os.path.basename(csv_file)
    expected_csv_name = f"{task}_{shot}-shot_submission.csv"
    print("Is:",csv_name, "Expected:", expected_csv_name)
    return csv_name == expected_csv_name


def sort_key(entry):
    # Extract task, shot, and experiment number from the entry
    parts = entry.split('/')
    task = parts[0]
    shot = int(parts[1].split('-')[0])
    exp_number = extract_exp_number(parts[2])
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
        model_dir = os.path.join(setting_directory, model_dir)

        # Skip/Delete if no event file
        event_file = get_event_file_from_model_dir(model_dir)
        if event_file is None:
            # TODO Delete if no event file!
            #print("No event file found, skipping..")
            continue

        # Skip if metric not in event file
        if not is_metric_in_event_file(event_file, metric_tags['map']):
            #print("Metric map not present, skipping..")
            continue

        # Skip if prediction csv file is present
        if contains_csv_file(task, shot, model_dir):
            continue

        model_dirs.append(model_dir)
    return model_dirs


work_dir_path = os.path.join("/scratch", "medfm", "medfm-challenge", "work_dirs")

tasks = ["colon", "endo", "chest"]
shots = ["1", "5", "10"]
#exps = [1, 2, 3, 4, 5]

metric_tags = {"auc": "AUC/AUC_multiclass",
               "aucl": "AUC/AUC_multilabe",
               "map": "multi-label/mAP",
               "agg": "Aggregate"}

report = [
    "\n---------------------------------------------------------------------------------------------------------------",
    f"| Valid Models without an existing prediction CSV file:",
    "---------------------------------------------------------------------------------------------------------------"]

if __name__ == "__main__":  # Important when using multiprocessing
    with Pool() as pool:
        combinations = [(task, shot) for task in tasks for shot in shots]

        # Use imap_unordered and directly iterate over the results
        results = []
        for result in pool.imap_unordered(process_task_shot_combination, combinations):
            results.append(result)

    model_dirs = []
    for task, shot, model_list in results:
        if model_list:
            model_dirs.extend(model_list)

    report_entries = [
        f"| {task}/{shot}-shot/exp{extract_exp_number(model.split(os.sep)[-1])}\t{model.split(os.sep)[-1]}"
        for task, shot, model_list in results if model_list for model in model_list
    ]
    report_entries = sorted(report_entries, key=sort_key)

    misplaced_csv_dir = "./submissions/evaluation"
    all_csv_files = find_all_csv_files_in_directory(misplaced_csv_dir)

    for model_dir in model_dirs:
        # Extract task, shot, and model_dir_name from the model_dir path
        model_dir_split = model_dir.split("work_dirs/")

        task = model_dir_split[0]
        shot = model_dir_split[1].split('-')[0]
        exp = extract_exp_number(model_dir_split[2])

        print(task, shot, exp)
        exit()

        for csv_file in all_csv_files:

            if matches_model_directory(csv_file, task, shot):
                correct_csv_path = os.path.join(model_dir, os.path.basename(csv_file))

                shutil.copy(csv_file, correct_csv_path)
                print(f"Copied {csv_file} to {correct_csv_path}")
                break  # Break once a match is found to avoid unnecessary further checking

    report = [
        "\n---------------------------------------------------------------------------------------------------------------",
        f"| Valid Models without an existing prediction CSV file:",
        "---------------------------------------------------------------------------------------------------------------",
        *report_entries,
        "---------------------------------------------------------------------------------------------------------------"
    ]

    for line in report:
        print(line)
