from setuptools import setup

setup(
    name='boost_colab',
    version='0.5.0',
    description='Boost your productivity with Google Colab',
    url='https://github.com/shuds13/pyexample',
    author='Matjaž Berčič',
    author_email='me@matbb.org',
    license='MIT',
    packages=['boost_colab'],
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
