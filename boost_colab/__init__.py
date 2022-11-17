"""
Easier Data Science with Google Colab

Simplifies project initialization from git and periodic data sync back to Google Drive.

Module variables set during session initialization:
CURRENT_SESSION : Current session type
CURRENT_PROJECT_NAME : Name of initialized project
CURRENT_PROJECT_PATH = Path to initialized project on disk 
CURRENT_MOUNTED_DATA_JOB_PATH = Path to project's data_job folder
CURRENT_MOUNTED_DATA_PROJECT_PATH = Path to project's data_project folder

set_logging(level) : sets logging to stdout at specified log level
identify_session() : identifies where current session is running
initialize(...) : initializes current project
run_sub_jobs(...) : runs specified number of sub-jobs in current session on Colab
decompress_if_not_exists(fname_zip) : unzips the file if decompressed file does not exist
compress_file(fname) : compresses the file with zip
stop_interactive_nb() : Stops interactive notebook execution by throwing an exception, 
    does nothing if not in interactive notebook
"""
import datetime
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from tkinter import CURRENT

import requests

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
SYNC_INTERVAL_S = None

# Current session type
CURRENT_SESSION = None
# Project name derived from git url
CURRENT_PROJECT_NAME = None
# Project path on disk
CURRENT_PROJECT_PATH = None
# Paths to data_job and data_project in Google Drive mounted folder
CURRENT_MOUNTED_DATA_JOB_PATH = None
CURRENT_MOUNTED_DATA_PROJECT_PATH = None
# Paths to data_job and data_project as returned during initialization
CURRENT_LOCAL_DATA_JOB_PATH = None
CURRENT_LOCAL_DATA_PROJECT_PATH = None

sync_loop_event = threading.Event()
sync_loop_count = 0


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


def _print_subprocess_error(msg, p):
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


def _run_check_ok(cmd_list, msg, throw=False, print_stdout=False):
    p = subprocess.run(
        cmd_list,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    if print_stdout:
        print(p.stdout.decode("ascii"))
    if p.returncode != 0:
        _print_subprocess_error(msg, p)
        if throw:
            raise RuntimeError(msg)
        return False
    return True


def identify_session():
    """
    Identify current session.

        Returns:
            colab                   : for session on google colab through its web interface
            remote-colab            : for remote kernell connection to colab
            notebook-qtconsole      : jupyter notebook or qtconsole
            ipython                 : ipython running in shell (not qtconsole)
            other                   : other
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

        Parameters:
            mount_point       : where Google Drive should be mounted
            sync_mounted_path : path inside Google Drive that is synced to local storage
            sync_local_path   : path outside Google Drive on local disk
            sync_interval_s   : sync interval in seconds

        Returns:
            None
    """
    global SYNC_THREAD, SYNC_STOP, SYNC_RSYNC_FLAGS, SYNC_INTERVAL_S
    logger.debug("Started background sync thread launch")
    try:
        if SYNC_THREAD is not None:
            SYNC_STOP = True
            SYNC_THREAD.join()
    except:
        pass
    SYNC_INTERVAL_S = sync_interval_s

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
            if p.returncode not in [0, 24]:
                # Ignoring return codes:
                # 24: files are deleted on source before sync is finished
                _print_subprocess_error("Synchronization loop: Error in sync", p)
            if lock.locked():
                lock.release()
            if p.returncode == 0:
                sync_loop_event.set()
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
    sync_data_project="full",
    force=False,
    project_name=None,
):
    """
    On Colab:
    Initializes project from git to /content/<project name> and starts periodic sync.
    If git_url is not set to None, project name is derived from git_url.
    If requirements file is not set to None, requirements are installed from within the project with pip.
    If notebooks folder is provided, changes working dir to that folder inside the project.

    If running outside Colab:
    Sets project path and returns.

        Parameters:
            git_url           : git compatible url, passed to git_clone
            job_name          : name of current job
            requirements_file : requirements file to install with pip relative to project root
            notebooks_folder  : folder at project root where notebooks are stored
            rsync_flags       : flags to pass to rsync when synchronizing project data to Google Drive
            sync_interval_s   : sync interval in seconds
            sync_data_project : how to sync data_project folder:
                                   full : full sync with supplied rsync flags
                                   TODO bindmount : mounts colab subfolder as readonly
                                   <list of files> : sync only these files
                                   no : do nothing
            project_name      : project name, used in case git_url is None
            force             : force initialization

        Returns:
            ( data_project, data_job ) : tuple of path strings to locations of where project shared data (ro) is stored, and where data from current job is stored
    """
    global SYNC_RSYNC_FLAGS
    global CURRENT_PROJECT_NAME, CURRENT_SESSION, CURRENT_PROJECT_PATH
    global CURRENT_LOCAL_DATA_JOB_PATH, CURRENT_LOCAL_DATA_PROJECT_PATH
    global CURRENT_MOUNTED_DATA_JOB_PATH, CURRENT_MOUNTED_DATA_PROJECT_PATH
    if rsync_flags is not None:
        SYNC_RSYNC_FLAGS = rsync_flags

    session = identify_session()
    CURRENT_SESSION = session
    if git_url is not None:
        project_name = re.match("^.*/([^/]*)$", git_url).groups()[0]
        project_name = (
            project_name[0:-4] if project_name.endswith(".git") else project_name
        )
    elif project_name is None:
        print("Error: if git_url is None, project_name has to be supplied")
        logger.error("Error: if git_url is None, project_name has to be supplied")
        return
    CURRENT_PROJECT_NAME = project_name
    project_path = os.path.join("/content", project_name)
    CURRENT_PROJECT_PATH = project_path + "/"

    CURRENT_LOCAL_DATA_PROJECT_PATH = os.path.join(project_path, "data_project") + "/"
    CURRENT_LOCAL_DATA_JOB_PATH = os.path.join(project_path, "data_job") + "/"

    def chdir_to_notebooks(is_colab=True):
        if is_colab:
            if notebooks_folder is None:
                os.chdir(os.path.join("/content", project_name))
            else:
                os.chdir(os.path.join("/content", project_name, notebooks_folder))
        else:
            if notebooks_folder is None:
                os.chdir(CURRENT_PROJECT_PATH)
            else:
                os.chdir(os.path.join(CURRENT_PROJECT_PATH, notebooks_folder))

    if session in ["colab", "remote-colab"]:
        if os.path.exists(project_path) and force == False:
            chdir_to_notebooks()
            if force == False:
                print("Initialization skipped: Already initialized on Colab")
                return CURRENT_LOCAL_DATA_PROJECT_PATH, CURRENT_LOCAL_DATA_JOB_PATH
            elif SUB_JOB_ENV_VAR in os.environ:
                print(
                    "Initialization skipped: Already initialized on Colab and in sub job"
                )
                return CURRENT_LOCAL_DATA_PROJECT_PATH, CURRENT_LOCAL_DATA_JOB_PATH
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
        wd_project = "." if notebooks_folder is None else ".."
        CURRENT_PROJECT_PATH = str(Path(wd_project).absolute()) + "/"
        CURRENT_LOCAL_DATA_PROJECT_PATH = (
            str(Path(wd_project + "/data_project").absolute()) + "/"
        )
        CURRENT_LOCAL_DATA_JOB_PATH = (
            str(Path(wd_project + "/data_job").absolute()) + "/"
        )
        chdir_to_notebooks(is_colab=False)
        return CURRENT_LOCAL_DATA_PROJECT_PATH, CURRENT_LOCAL_DATA_JOB_PATH

    logger.info("Initialization started")
    if git_url is not None:
        # git clone project (print stdout, stderr)
        _run_check_ok(
            cmd_list=["git", "clone", git_url, project_path],
            msg="Error inicializing from git",
            throw=True,
        )
        logger.info("Initialization: git clone complete, last commit:")
        chdir_to_notebooks()
        _run_check_ok(
            cmd_list=[
                "git",
                "log",
                "--name-status",
                "HEAD^..HEAD",
            ],
            msg="Error printing last commit",
            throw=True,
            print_stdout=True,
        )
    else:
        logger.info("Skipping initialization from git")
        chdir_to_notebooks()

    # sync mount
    CURRENT_MOUNTED_DATA_JOB_PATH = (
        os.path.join(
            "/content/drive/MyDrive/colab_data",
            project_name,
            "data_job",
            job_name,
        )
        + "/"
    )
    _sync_mount_google_drive(
        mount_point="/content/drive",
        sync_mounted_path=CURRENT_MOUNTED_DATA_JOB_PATH,
        sync_local_path=project_path + "/data_job",
        sync_interval_s=sync_interval_s,
    )
    logger.info("Initialization: sync mount started")

    # sync data_project
    sync_local_path = project_path + "/data_project"
    CURRENT_MOUNTED_DATA_PROJECT_PATH = (
        os.path.join(
            "/content/drive/MyDrive/colab_data",
            project_name,
            "data_project",
        )
        + "/"
    )
    Path(sync_local_path).mkdir(parents=True, exist_ok=True)
    Path(CURRENT_MOUNTED_DATA_PROJECT_PATH).mkdir(parents=True, exist_ok=True)
    if sync_data_project == "full":
        _run_check_ok(
            cmd_list=[
                "rsync",
            ]
            + SYNC_RSYNC_FLAGS
            + [
                CURRENT_MOUNTED_DATA_PROJECT_PATH,
                sync_local_path,
            ],
            msg="Error initially syncing data_project",
            throw=True,
        )
    elif sync_data_project == "no":
        pass
    elif type(sync_data_project) == list:
        for fname in sync_data_project:
            f_src = os.path.join(CURRENT_MOUNTED_DATA_PROJECT_PATH, fname)
            f_dst = os.path.join(sync_local_path, fname)
            f_dirpath = os.path.dirname(f_dst)
            os.makedirs(f_dirpath, exist_ok=True)
            shutil.copy(f_src, f_dst)
    else:
        raise RuntimeError(
            "Wrong argument for sync_data_project : " + sync_data_project
        )
    CURRENT_MOUNTED_DATA_PROJECT_PATH += "/"
    logger.info("Initialization: initial data_project download complete")

    # pip install requirements if requirements are in project path
    if requirements_file is not None:
        requirements_full_path = os.path.join(
            "/content", project_name, requirements_file
        )
        _run_check_ok(
            cmd_list=["pip", "install", "-r", requirements_full_path],
            msg="Error installing requirements from pip",
            throw=True,
        )
    logger.info("Initialization: requirements installed")

    print("Successfully initialized the project")
    return CURRENT_LOCAL_DATA_PROJECT_PATH, CURRENT_LOCAL_DATA_JOB_PATH


def run_sub_jobs(
    n_sub_jobs,
    data_job,
    first_job_to_run=0,
    completion_file=None,
):
    """
    Runs specified number of sessions.
    In each session, returns session number and session data storage location,
    the equivalent of data_job for the sub-job.

    Sessions or sub-jobs are the same notebook running with a different job-id,
    in sequnece 0, ... n_sub_jobs-1
    If first_job_to_run is provided, subjobs start with this index to n_sub_jobs-1
    If completion_file is provided, jobs where this file exists in sub-job folder are skipped.

    In main session, function waits untill all sub jobs are complete and finally returns with n_sub_jobs.

    The benefit is that this way the tf GPU allocation is cleaned between each run.
    Downsides: the jupyter kernel is not the current kernel.

    Presumptions: google drive is mounted in /content/drive
    and the current notebook is the script with the same name in the Colab Notebooks folder
    in google drive.

        Parameters:
            n_sub_jobs       : number of sub jobs to run
            data_job         :  data_job folder before sub-jobs are started
            first_job_to_run : if set jobs before this one are skipped
            completion_file  : if set, when this file exists inside sub-job folder, sub-job is skipped

        Returns:
            ( i_current_job, data_job ) : tuple index of current job, data_job for current sub-job
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
    nb_filename = urllib.parse.unquote(nb_filename)
    nb_full_path = "/content/drive/MyDrive/Colab Notebooks/" + nb_filename
    current_env = {k: v for k, v in os.environ.items()}
    dt_started_run = datetime.datetime.now()
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
            f.write("Group started at   : " + str(dt_started_run) + "\n")

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
            _print_subprocess_error("Sub-job id {:d} : Error".format(i_sub_job), p)
            break

        time.sleep(1.0)  # Wait for FS sync

        print("Completed sub job : ", i_sub_job)
        logger.info("Completed sub-job {:d}".format(i_sub_job))

    with open(data_job + "/current_sub_job_is.txt", "wt") as f:
        f.write("{:d}\n".format(i_sub_job))
    logger.info("Completed all sub-jobs, returning in main job")
    return n_sub_jobs, data_job


def decompress_if_not_exists(fname_zip, archive="zip"):
    """
    De-compresses file with zip utility if decompressed file does not exist.
    Does nothing if decompressed file already exists.

        Parameters:
            fname: file to decompress, ends with .zip
        Returns:
            file name of decompressed file
    """
    assert archive in ["zip", "gz"]
    fout = fname_zip.replace("." + archive, "")
    fout_folder = os.path.split(fout)[0]

    if not os.path.isfile(fout):
        print("De compressing ", fname_zip, " -> ", fout, " # ", fout_folder)
        if archive == "zip":
            _run_check_ok(
                ["unzip", "-j", fname_zip, "-d", fout_folder],
                msg="zip decompression error",
            )
        else:
            _run_check_ok(
                [
                    "gunzip",
                    fname_zip,
                ],
                msg="gzip decompression error",
            )

    return fout


def compress_file(fname, archive="zip"):
    """
    Compresses file with zip utility.
    """
    assert archive in [
        "zip",
        "gz",
    ]
    print("Compressing file ", fname)
    fout = fname + "." + archive
    if archive == "zip":
        _run_check_ok(["zip", "-j", fout, fname], msg="zip compression error")
    else:
        _run_check_ok(["gzip", fname], msg="gzip compression error")


class _StopExecution(Exception):
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
    raise _StopExecution


def copy_to_cloud_gdrive(local_data_fname_path):
    """
    Copies a file to data_project folder inside mounted gdrive.
    :param local_data_fname_path: local file path, relative to data_job folder
    """
    if CURRENT_MOUNTED_DATA_PROJECT_PATH is not None:
        src = os.path.join(CURRENT_LOCAL_DATA_JOB_PATH, local_data_fname_path)
        dst = os.path.join(CURRENT_MOUNTED_DATA_PROJECT_PATH, local_data_fname_path)
        Path(os.path.dirname(dst)).mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        logger.info(f"Copied file {local_data_fname_path} to cloud gdrive")
    else:
        logger.info(
            f"Outside of colab: not copying {local_data_fname_path} to cloud gdrive"
        )


def copy_to_persistent_project_storage(local_data_fname_path):
    """
    Copies a file to persistent data_project folder:
    - in colab / remote-colab: inside mounted gdrive
    - otherwise: inside data_project
    :param local_data_fname_path: local file path, relative to data_job folder
    """
    assert CURRENT_SESSION is not None
    if CURRENT_SESSION in ["colab", "remote-colab"]:
        copy_to_cloud_gdrive(local_data_fname_path)
    else:
        src = os.path.join(CURRENT_LOCAL_DATA_JOB_PATH, local_data_fname_path)
        dst = os.path.join(CURRENT_LOCAL_DATA_PROJECT_PATH, local_data_fname_path)
        Path(os.path.dirname(dst)).mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        logger.info(f"Copied file {local_data_fname_path} to local data_project folder")


def crash_kernel():
    """
    Crashes the kernel in order to force-stop execution and runtime.
    Might save runtime credits.
    """

    import ctypes

    p = ctypes.pointer(ctypes.c_char.from_address(5))
    p[0] = b"x"


def wait_for_sync():
    """
    Wait until data upload synchronization loop successfully executes at least 1 time

    Returns False if sync is not running.
    """
    if SYNC_THREAD is None:
        return False

    sync_loop_event.clear()
    for i in range(2):
        sync_loop_event.wait()
        time.sleep(SYNC_INTERVAL_S / 2)
    return True
