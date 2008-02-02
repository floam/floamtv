#!/usr/bin/env python

from distutils.core import setup
from os import system

setup(name='floamtv',
      url='http://aarongyes.com/stuff/floamtv',
      version='0.2bzr',
      py_modules=['fuzzydict'],
      scripts=['floamtv.py'],
      packages=['simplejson'],
      author='Aaron Gyes',
      author_email='floam@sh.nu'
)