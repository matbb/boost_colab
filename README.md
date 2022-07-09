Easier Data Science with Google Colab
=====================================

Makes using Google Colab feel more like a job queue.

Functions:
* Quickly clone and install dependencies of your project.
* Periodically sync back your progress to google drive
  (one input folder per project, one output folder per project/job)
* Run many sub-jobs in the background 
  (like `fork`-ing before GPU allocation, to free GPU resources after each experiment)
* Stop notebook execution at specified line (like `sys.exit` for notebooks)
* Upload notebook to Colab with specified runtime allocation (requires properly configured rclone remote)

Example usage:
```python3
!pip install git+ssh://git@github.com/matbb/boost_colab.git
import boost_colab

if True:
    import logging
    boost_colab.set_logging(logging.DEBUG)

job_name = "test-job"
data_project, data_job = boost_colab.initialize(
  git_url="git@github.com/matbb/boost_colab.git",
  job_name=job_name,
)

# run sub jobs in background, stops execution after jobs finish
import os
test_input_file =  data_project + "/test_input_file.txt"
if os.path.isfile(test_input_file):
    with open(test_input_file,"r") as f:
        input_content = f.read()
else:
    print("Input file {:s} does not exist, using test content".format(test_input_file))
    input_content = "TEST INPUT"

# Run this notebook 5 times in background:
i_sub_job, data_job = boost_colab.run_sub_jobs(n_sub_jobs=5,data_job=data_job)
print("Project data location in sub-job: ", data_job)

with open(data_job + "/test.txt", "wt") as f_out:
    f_out.write("{:s} : Running sub-job {:d}".format(input_content, i_sub_job))


boost_colab.stop_execution()
# This will not run. Useful to stop execution 
# after this line when selecting "run all cells" in notebook

with open(data_job + "/test_after_exit.txt", "wt") as f_out:
    f_out.write("{:s} : Running sub-job {:d}".format(input_content, i_sub_job))
```
ran from a notebook named "01-mb-test-colab-boost-v01.ipynb" stored in folder `notebooks` in your project,
where the input file
```
colab_data/<project name>/data_project/test_input_file.txt
```
in your colab drive has content `test input`,
will produce the following file structure:

```
colab_data/<project name>/data_job/test-job/sub_job_000/test.txt
colab_data/<project name>/data_job/test-job/sub_job_001/test.txt
colab_data/<project name>/data_job/test-job/sub_job_002/test.txt
colab_data/<project name>/data_job/test-job/sub_job_003/test.txt
colab_data/<project name>/data_job/test-job/sub_job_004/test.txt
```
with contents in consecutive files
```
test_input : Running sub-job 0
test_input : Running sub-job 1
test_input : Running sub-job 2
test_input : Running sub-job 3
test_input : Running sub-job 4
```
.

In simple words: provided that

* your notebook can be run as a script 
  (magic commands still work, but not interactive widgets and such)
* your input data is in `colab_data/<project name>/data_project/`
  (available under `/content/<project name>/data_project` during runtime)
* your output data is written to `/content/<project name>/data_job` 
  (synced to `colab_data/<project_name>/data_job/<job-name>/` in your google drive)
* your notebooks are in folder called `notebooks` in your project
* your project's requirements are in a file called `requirements.txt` at the project's root

this project will help you run many jobs and sync all the results back to your google drive periodically.
In this way in case your can keep the model's progress in case your colab runtime is terminated during training,
and can run many experiments in one session (useful with background enabled sessions in colab pro).

The resulting notebooks from sub-job runs are stored in sub-job folders.
In the main data folder there is a file `current_sub_job_is.txt` indicating the currently running sub-job.

## Uploading notebook 

Requires properly configured rclone remote.

Install optional dependencies
```bash
pip install nbconvert
```
Uploading notebook with
```python
python3 -m boost_colab --verbose \
  --local-filename=./notebooks/01-mb-test.ipynb \
  --job-name=test-job-42 \
  --high-ram \
  --accelerator=gpu \
  --background-execution
```
will also configure default runtime for the notebook to high-ram, gpu-accelerated instance with background execution enabled.
In case the first cell in the notebook contains setting of the variable `job_name`,
this variable will be set to the value provided on the command line.

# How it works

## Data sync
Google drive is mounted to `/content/drive` in Colab.
Folder `/content/<project name>/data_project` is synced from drive on startup (one-way).
Folder `/content/<project name>/data_job` is synced from drive on startup and then periodically synced back to drive into `colab_data/<project name>/data_job/<job name>`.

Variables `data_project` and `data_job` hold these locations, in case of running sub-jobs each sub-job holds its assigned subfolder of `data_job`.

**WARNING**: data is by default synced with `rsync --delete`. In case you want to avoid this, configure rsync flags during initialization.


## Sub-jobs
`jupyter-nbconvert` runs the current notebook as fetched from google drive (not your project's git) in the background.
Environment variables are used to determine which sub-job is running.

## Working locally
When working locally
`data_job` and `data_project` point to folders at the root of current project.

## Trash folder on Google Drive

Be mindful of your trash folder. In case you are keeping last n checkpoints of your model and deleting the older checkpoints,
the deleted files end up in your trash folder and consume your drive space.
Unfortunately it is not possible to bypass trash when deleting files from google drive.

You might want to set up auto eptying of trash folder.
See [this stackoverflow post](https://stackoverflow.com/questions/32749289/automatically-delete-file-from-google-drive-trash)
for more information.

