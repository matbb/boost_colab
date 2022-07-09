import subprocess
import threading
from pathlib import Path
import os, time, sys, signal
from tkinter import CURRENT
import requests
import re
import datetime

import logging

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


def set_logging(level=logging.DEBUG):
    global logger
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s %(name)-12s %(levelname)-8s %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(level)


try:
    from google.colab import drive
except ImportError:
    logger.warning("WARNING: not running on Colab")


def print_subprocess_error(msg, p):
    for f_print in [print, logger.error]:
        f_print(msg)
        f_print(
            "Return code: {:d}\n"
            "\nStdout:\n{:s}"
            "\nStderr:\n{:s}".format(
                p.returncode,
                p.stdout.decode("ascii") if p.stdout else "None",
                p.stderr.decode("ascii") if p.stderr else "None",
            )
        )


def run_check_ok(cmd_list, msg, throw=False):
    p = subprocess.run(
        cmd_list,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    if p.returncode != 0:
        print_subprocess_error(msg, p)
        if throw:
            raise RuntimeError(msg)
        return False
    return True


JOB_ENV_VAR = "BOOST_COLAB_JOB_NAME"
SUB_JOB_ENV_VAR = "SUB_JOB_ID"
SUB_JOB_FOLDER_NAME = "sub_job"
SUB_JOB_FOLDER_FMT = SUB_JOB_FOLDER_NAME + "_{:03d}"

SYNC_THREAD = None
SYNC_STOP = False
SYNC_RSYNC_FLAGS = [
    "-av",
    "--delete",
]

CURRENT_SESSION = None
CURRENT_PROJECT_NAME = None
CURRENT_PROJECT_PATH = None


def identify_session():
    """
    Identify current session.
    Returns:
        colab         : for session on google colab through its web interface
        remote-colab  : for remote kernell connection to colab
        notebook-qtconsole      : jupyter notebook or qtconsole
        ipython       : ipython running in shell (not qtconsole)
        other         : other
    """

    try:
        from google.colab import output

        w = output.eval_js("window.location.href", timeout_sec=1)
        if w is None:
            return "remote-colab"  # used from console
        elif "google" in w:
            return "colab"
        else:
            return "remote-colab"  # used from browser

    except Exception as e:
        try:
            from IPython import get_ipython

            shell = get_ipython().__class__.__name__
            if shell == "ZMQInteractiveShell":
                return "notebook-qtconsole"  # Jupyter notebook or qtconsole
            elif shell == "TerminalInteractiveShell":
                return "ipython"  # Terminal running IPython
        except NameError:
            pass
        except ModuleNotFoundError:
            pass
    return "other"


if identify_session() in [
    "colab",
    "remote-colab",
]:
    subprocess.run(["apt-get", "install", "-y", "rsync"])


def _sync_mount_google_drive(
    mount_point,
    sync_mounted_path,
    sync_local_path,
    sync_interval_s=30,
):
    """
    Mount and re-mount google drive on mount_point,
    and periodically sync files between sync_mounted_path and sync_local_path using rsync

    Returns after initial sync is done.
    """
    global SYNC_THREAD, SYNC_STOP, SYNC_RSYNC_FLAGS
    logger.debug("Started background sync thread launch")
    try:
        if SYNC_THREAD is not None:
            SYNC_STOP = True
            SYNC_THREAD.join()
    except:
        pass

    if sync_mounted_path[-1] != "/":
        sync_mounted_path = sync_mounted_path + "/"
    if sync_local_path[-1] != "/":
        sync_local_path = sync_local_path + "/"

    lock = threading.Lock()
    lock.acquire()

    Path(sync_local_path).mkdir(parents=True, exist_ok=True)

    def f(initialize=True):
        logger.debug("Launched main background thread loop")
        global SYNC_THREAD, SYNC_STOP, SYNC_RSYNC_FLAGS
        while True:
            # 1. check if mount point is mounted
            if os.path.isdir(os.path.join(mount_point, "MyDrive")) == False:
                logger.debug("Mounting google drive")
                print("Mounting google drive")
                drive.mount(mount_point)
                time.sleep(1.0)
                Path(sync_mounted_path).mkdir(parents=True, exist_ok=True)
            # 2. synchronize
            if initialize:
                logger.debug("Synchronization loop: initialization")
                initialize = False
                fs_from = sync_mounted_path
                fs_to = sync_local_path
            else:
                logger.debug("Synchronization loop: upload")
                fs_from = sync_local_path
                fs_to = sync_mounted_path
            p = subprocess.run(
                [
                    "rsync",
                ]
                + SYNC_RSYNC_FLAGS
                + [
                    fs_from,
                    fs_to,
                ],
            )
            if p.returncode not in [0,24]:
                # Ignoring return codes:
                # 24: files are deleted on source before sync is finished
                print_subprocess_error("Synchronization loop: Error in sync", p)
            if lock.locked():
                lock.release()
            time.sleep(sync_interval_s)
            if SYNC_STOP:
                SYNC_THREAD = None
                SYNC_STOP = False
                logger.debug("Stopping sync thread")
                break

    SYNC_THREAD = threading.Thread(target=f)
    SYNC_THREAD.start()
    lock.acquire()
    logger.debug("Finished background sync thread launch")


def initialize(
    git_url,
    job_name,
    requirements_file="requirements.txt",
    notebooks_folder="notebooks",
    rsync_flags=None,
    sync_interval_s=30,
    force=False,
):
    # if session is not first run in colab (no job name env variable, no subjob env variable)
    # then just change workdir and exit with a message.
    # If we are on colab, then cd to "notebooks" folder.
    # If this is first such session (folder with project name does not exist yet)
    # then we initialize, otherwise we exit
    global SYNC_RSYNC_FLAGS
    global CURRENT_PROJECT_NAME, CURRENT_SESSION, CURRENT_PROJECT_PATH
    if rsync_flags is not None:
        SYNC_RSYNC_FLAGS = rsync_flags

    session = identify_session()
    CURRENT_SESSION = session
    project_name = re.match("^.*/([^/]*)$", git_url).groups()[0]
    project_name = project_name[0:-4] if project_name.endswith(".git") else project_name
    CURRENT_PROJECT_NAME = project_name
    project_path = os.path.join("/content", project_name)
    CURRENT_PROJECT_PATH = project_path
    data_project = os.path.join(project_path, "data_project")
    data_job = os.path.join(project_path, "data_job")

    if session in ["colab", "remote-colab"]:
        if os.path.exists(project_path) and force == False:
            os.chdir(os.path.join("/content", project_name, notebooks_folder))
            if force == False:
                print("Initialization skipped: Already initialized on Colab")
                return data_project, data_job
            elif SUB_JOB_ENV_VAR in os.environ:
                print(
                    "Initialization skipped: Already initialized on Colab and in sub job"
                )
                return data_project, data_job
        for fname in [
            "/etc/environment",
            "/etc/profile",
            "/etc/bash.bashrc",
            "/root/.bashrc",
        ]:
            with open(fname, "at") as f:
                f.write('\n{:s}="{:s}"\n'.format(JOB_ENV_VAR, job_name))
        os.environ[JOB_ENV_VAR] = job_name
    else:
        print("Initialization skipped: Not running inside Colab")
        CURRENT_PROJECT_PATH = str(Path("..").absolute())
        return str(Path("../data_project").absolute()), str(
            Path("../data_job").absolute()
        )

    logger.info("Initialization started")
    # git clone project (print stdout, stderr)
    run_check_ok(
        cmd_list=["git", "clone", git_url, project_path],
        msg="Error inicializing from git",
        throw=True,
    )
    os.chdir(os.path.join("/content", project_name, notebooks_folder))
    logger.info("Initialization: git clone complete")

    # sync mount
    _sync_mount_google_drive(
        mount_point="/content/drive",
        sync_mounted_path=os.path.join(
            "/content/drive/MyDrive/colab_data",
            project_name,
            "data_job",
            job_name,
        ),
        sync_local_path=project_path + "/data_job",
        sync_interval_s=sync_interval_s,
    )
    logger.info("Initialization: sync mount started")

    # sync data_project
    sync_local_path = project_path + "/data_project"
    sync_mounted_path = (
        os.path.join(
            "/content/drive/MyDrive/colab_data",
            project_name,
            "data_project",
        )
        + "/"
    )
    Path(sync_local_path).mkdir(parents=True, exist_ok=True)
    Path(sync_mounted_path).mkdir(parents=True, exist_ok=True)
    run_check_ok(
        cmd_list=[
            "rsync",
        ]
        + SYNC_RSYNC_FLAGS
        + [
            sync_mounted_path,
            sync_local_path,
        ],
        msg="Error initially syncing data_project",
        throw=True,
    )
    logger.info("Initialization: initial data_project download complete")

    # pip install requirements if requirements are in project path
    if requirements_file is not None:
        requirements_full_path = os.path.join(
            "/content", project_name, requirements_file
        )
        run_check_ok(
            cmd_list=["pip", "install", "-r", requirements_full_path],
            msg="Error installing requirements from pip",
            throw=True,
        )
    logger.info("Initialization: requirements installed")

    print("Successfully initialized the project")
    return data_project, data_job


def run_sub_jobs(
    n_sub_jobs,
    data_job,
    first_job_to_run=0,
    completion_file=None,
    ignore_killed_sub_jobs=False,
):
    """
    Runs specified number of sessions.
    In each session, returns session number and session data storage location,
    the equivalent of data_job for the sub-job.

    Sessions or sub-jobs are the same notebook running with a different job-id,
    in sequnece 0, ... n_sub_jobs-1
    If first_job_to_run is provided, subjobs start with this index to n_sub_jobs-1
    If completion_file is provided, jobs where this file exists in sub-job folder are skipped.
    If ignore_killed_sub_jobs=True no error is reported if sub-job is killed (useful if notebook uses stop_execution)

    In main session, function waits untill all sub jobs are complete and finally returns with n_sub_jobs.

    The benefit is that this way the tf GPU allocation is cleaned between each run.
    Downsides: the jupyter kernel is not the current kernel.

    Presumptions: google drive is mounted in /content/drive
    and the current notebook is the script with the same name in the Colab Notebooks folder
    in google drive.
    """

    def get_sub_job_folder(i_sub_job):
        return os.path.join(data_job, SUB_JOB_FOLDER_FMT.format(i_sub_job))

    try:
        i_sub_job = int(os.environ[SUB_JOB_ENV_VAR])
        sub_job_folder = get_sub_job_folder(i_sub_job)
        logger.info(
            "run_sub_jobs: Running inside sub-job {:d}, in folder {:s}, returning".format(
                i_sub_job, sub_job_folder
            )
        )
        return i_sub_job, sub_job_folder
    except KeyError:
        pass

    current_env = {k: v for k, v in os.environ.items()}
    nb_filename = requests.get("http://172.28.0.2:9000/api/sessions").json()[0]["name"]
    nb_full_path = "/content/drive/MyDrive/Colab Notebooks/" + nb_filename
    current_env = {k: v for k, v in os.environ.items()}
    for i_sub_job in range(n_sub_jobs):
        if i_sub_job < first_job_to_run:
            logger.info(
                "run_sub_jobs: Skipping sub-job {:d}, is before first job".format(
                    i_sub_job
                )
            )
            continue
        sub_job_folder = get_sub_job_folder(i_sub_job)
        if (completion_file is not None) and os.path.isfile(
            os.path.join(sub_job_folder, completion_file)
        ):
            logger.info(
                "run_sub_jobs: Skipping sub-job {:d}, completion file exists".format(
                    i_sub_job
                )
            )
            continue
        print("Running sub-job : ", i_sub_job)
        logger.info("Running sub-job : {:d}".format(i_sub_job))
        with open(data_job + "/current_sub_job_is.txt", "wt") as f:
            f.write("{:d}\n".format(i_sub_job))
            f.write("Sub-job started at : " + str(datetime.datetime.now()) + "\n")

        current_env[SUB_JOB_ENV_VAR] = str(i_sub_job)
        logger.info("Creating sub-job folder: " + sub_job_folder)
        Path(sub_job_folder).mkdir(parents=True, exist_ok=True)
        p = subprocess.run(
            [
                "jupyter",
                "nbconvert",
                "--ExecutePreprocessor.timeout=-1",
                "--to",
                "notebook",
                "--output",
                sub_job_folder + "/" + nb_filename,
                "--execute",
                nb_full_path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=current_env,
            cwd=os.getcwd(),
        )
        logger.info(
            "Sub-job {:d} completed with return code {:d}".format(
                i_sub_job, p.returncode
            )
        )
        if p.returncode != 0:
            kernel_died = (
                "nbconvert.preprocessors.execute.DeadKernelError: Kernel died"
                in p.stderr.decode("ascii")
            )

            if kernel_died and ignore_killed_sub_jobs == False:
                print_subprocess_error(
                    "Sub-job id {:d} : Kernel died, this might be expected".format(
                        i_sub_job
                    ),
                    p,
                )
            if not kernel_died:
                print_subprocess_error("Sub-job id {:d} : Error".format(i_sub_job), p)
                break

        time.sleep(1.0)  # Wait for FS sync

        print("Completed sub job : ", i_sub_job)
        logger.info("Completed sub-job {:d}".format(i_sub_job))

    with open(data_job + "/current_sub_job_is.txt", "wt") as f:
        f.write("{:d}\n".format(i_sub_job))
    logger.info("Completed all sub-jobs, returning in main job")
    return n_sub_jobs, data_job


def decompress_if_not_exists(fname):
    fout = fname.replace(".zip", "")
    fout_folder = os.path.split(fout)[0]

    if not os.path.isfile(fout):
        print("De compressing ", fname, " -> ", fout, " # ", fout_folder)
        run_check_ok(
            ["unzip", "-j", fname, "-d", fout_folder], msg="zip decompression error"
        )

    return fout


def compress_file(fname):
    print("Compressing file ", fname)
    fout = fname + ".zip"
    run_check_ok(["zip", "-j", fout, fname], msg="zip compression error")


class StopExecution(Exception):
    def _render_traceback_(self):
        pass


def stop_interactive_nb():
    """
    In main job pauses execution when running in interactive notebook
    (colab, remote-colab, qtconsole or jupyter)
    by raising an exception.

    In sub-jobs or scripts (python, ipython) does nothing.
    """
    if SUB_JOB_ENV_VAR in os.environ:
        return
    session = identify_session()
    if session not in ["colab", "remote-colab", "notebook-qtconsole"]:
        return
    logger.info("Intentionally stopping notebook execution")
    print("Intentionally stopping notebook execution")
    raise StopExecution
