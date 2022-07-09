#!/usr/bin/env python
# -*- coding: utf-8 -*-
from setuptools import setup

setup(
    name='boost_colab',
    version='0.5.2',
    description='Boost your productivity with Google Colab',
    url='https://github.com/matbb/boost_colab',
    author='Matjaž Berčič',
    author_email='me@matbb.org',
    license='MIT',
    packages=['boost_colab'],
    download_url = 'https://github.com/matbb/boost_colab/archive/refs/tags/v0.5.2.tar.gz',
    keywords = [ "Colab", "jupyter", ],
    install_requires=[
        "requests>=2.23",
    ],
    python_requires='>=3.5',
    classifiers=[
        'Development Status :: 1 - Planning',
        'Intended Audience :: Science/Research',
        'License :: OSI Approved :: MIT License',
        'Operating System :: POSIX :: Linux',
        'Programming Language :: Python :: 3',
    ],
)
