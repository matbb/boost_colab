#!/usr/bin/env python3
import argparse
import logging
import subprocess
import sys
import re
import tempfile
import os


def _get_project_name(git_url):
    project_name = re.match("^.*/([^/]*)$", git_url).groups()[0].strip().rstrip()
    project_name = project_name[0:-4] if project_name.endswith(".git") else project_name
    return project_name


def _get_git_project_name():
    p = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    if p.returncode != 0:
        logger.error("Could not determine project name from git")
        return None
    else:
        git_url = p.stdout.decode("ascii")
        return _get_project_name(git_url)


try:
    import nbformat
except ImportError:
    print("Error: to use notebook upload functionality, install nbformat")

if __name__ == "__main__":

    JOB_NAME_PATTERN = r"(?P<head>^\s*job_name\s*=\s*)(?P<quote>(\"|'|\"\"\"|'''))(?P<name>.*)(?P=quote)(?P<tail>.*)"

    parser = argparse.ArgumentParser(
        "Upload notebook to google colab and prepare it for running remotely"
    )

    parser.add_argument(
        "--action",
        required=True,
        type=str,
        choices=[
            "nbupload",
            "pull-data-job",
            "push-data-job",
            "pull-data-project",
            "push-data-project",
        ],
        help="Action to take: upload a notebook or pull/push data",
    )

    parser.add_argument(
        "--local-filename",
        required=False,
        type=str,
        help="Notebook to upload",
    )

    parser.add_argument(
        "--colab-filename",
        required=False,
        type=str,
        default=None,
        help="Filename on google colab, defaults to the same name as local file. If --job-name is specified, job name is appended to the filename",
    )

    parser.add_argument(
        "--job-name",
        required=False,
        default=None,
        type=str,
        help="Job name used in notebook and appended to uploaded filename",
    )

    parser.add_argument(
        "--project-name",
        required=False,
        default=None,
        type=str,
        help="If set, script is uploaded in project's folder. If unset, project name is obtained from git url or set to no project",
    )

    parser.add_argument(
        "--rclone-remote-name",
        required=False,
        default="gdrivecolab",
        type=str,
        help="rclone remote name",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        default=False,
        action="store_true",
        help="Enable logging",
    )

    colab_options = parser.add_argument_group("Colab runtime options")

    colab_options.add_argument(
        "--accelerator",
        type=str,
        choices=["gpu", "tpu"],
        default=None,
        help="Accelerator to use",
    )

    colab_options.add_argument(
        "--background-execution",
        default=False,
        action="store_true",
        help="Enable background execution for this notebook",
    )

    colab_options.add_argument(
        "--high-ram",
        default=False,
        action="store_true",
        help="Request a high-ram runtime",
    )

    args = parser.parse_args()

    logger = logging.getLogger()
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.CRITICAL)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)6s|%(process)5d| %(message)s"
    )
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    def set_logging(start=True):
        if start:
            logger.setLevel(logging.DEBUG)
        else:
            logger.setLevel(logging.CRITICAL)

    if args.action in [
        "pull-data-job",
        "push-data-job",
        "pull-data-project",
        "push-data-project",
    ]:
        project_name = args.project_name or _get_git_project_name()
        if project_name is None or len(project_name) == 0:
            logger.error(
                "When pulling or pushing data project name must be set or obtainable from git"
            )
            sys.exit(1)

        colab_path = "{:s}:colab_data/{:s}/".format(
            args.rclone_remote_name, project_name
        )

        if "data-job" in args.action:
            if not args.job_name:
                logger.error("When using data-job, job_name must be specified")
                sys.exit(1)
            colab_path = colab_path + "data_job/{:s}/".format(args.job_name)
            local_path = "data_job/"
        else:
            colab_path = colab_path + "data_project/"
            local_path = "data_project/"

        if "pull-" in args.action:
            from_path = colab_path
            to_path = local_path
        else:
            from_path = local_path
            to_path = colab_path

        p = subprocess.run(
            [
                "rclone",
                "copyto",
                "-v",
                "--progress",
                from_path,
                to_path,
            ],
        )
        assert (
            p.returncode == 0
        ), "ERROR: install and configure rclone to use notebook upload functionallity"
        sys.exit(0)

    elif args.action == "nbupload":
        assert args.local_filename

        colab_filename_parts = os.path.split(args.local_filename)
        colab_filename = colab_filename_parts[-1]
        if args.colab_filename is not None:
            colab_filename = args.colab_filename

        nb = nbformat.read(args.local_filename, as_version=nbformat.NO_CONVERT)

        # Check if first cell matches pattern, if it does update the content, otherwise add a cell above current cell
        if args.job_name is not None:
            first_cell = nb.cells[0]["source"]

            parts = colab_filename.split(".")
            colab_filename = "".join(
                parts[:-1] + ["-" + args.job_name] + ["." + parts[-1]]
            )

            nb.cells[0]["source"] = re.sub(
                JOB_NAME_PATTERN,
                r"\g<head>\g<quote>" + args.job_name + r"\g<quote>\g<tail>",
                nb.cells[0]["source"],
                flags=re.MULTILINE,
            )
            if first_cell != nb.cells[0]["source"]:
                logger.info("First cell was updated with supplied job name:")
                if args.verbose:
                    print(nb.cells[0]["source"])

        if args.project_name is None:
            p = subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
            )
            if p.returncode != 0:
                logger.error("Could not determine project name from git")
                project_name = ""
            else:
                git_url = p.stdout.decode("ascii")
                project_name = _get_project_name(git_url)
            project_name = _get_git_project_name() or ""
        else:
            project_name = args.project_name

        # Colab default "metadata" configuratio for colab
        notebook_metadata = {
            "colab": {
                "collapsed_sections": [],
                "name": colab_filename,
                "provenance": [],
            },
            "kernelspec": {"display_name": "Python 3", "name": "python3"},
            "language_info": {"name": "python"},
        }

        if project_name != "":
            colab_full_filename = project_name + "/" + colab_filename
        else:
            colab_full_filename = colab_filename

        if args.accelerator is not None:
            if args.accelerator == "gpu":
                notebook_metadata["accelerator"] = "GPU"
            else:
                notebook_metadata["accelerator"] = "TPU"

        if args.high_ram:
            notebook_metadata["colab"]["machine_shape"] = "hm"
            pass

        if args.background_execution:
            notebook_metadata["colab"]["background_execution"] = "on"

        nb.metadata = notebook_metadata

        with tempfile.TemporaryDirectory() as tmp:
            os.chmod(tmp, 0o777)
            ftmp_path = os.path.join(tmp, colab_filename)
            nbformat.write(nb, ftmp_path, version=nbformat.NO_CONVERT)

            p = subprocess.run(
                [
                    "rclone",
                    "copyto",
                    "-v",
                    "--progress",
                    ftmp_path,
                    "{:s}:Colab Notebooks/{:s}".format(
                        args.rclone_remote_name, colab_full_filename
                    ),
                ],
            )
            assert (
                p.returncode == 0
            ), "ERROR: install and configure rclone to use notebook upload functionallity"

        print(
            "After starting the notebook user interaction is needed to mount google drive"
        )
