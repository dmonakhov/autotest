#!/usr/bin/python

import os
from autotest.client import utils

version = 1

def setup(srcdir, tarball='fio-2.0.9-38-g98dc.tar.gz'):
    topdir = os.getcwd()
    utils.extract_tarball_to_dir(tarball, srcdir)
    os.chdir(srcdir)
    utils.make('-j4')
    utils.make('install')
    os.chdir(topdir)

srcdir = os.path.abspath('./src')
utils.update_version(srcdir, False, version, setup, srcdir)
