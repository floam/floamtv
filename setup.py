#!/usr/bin/env python

try:
   from setuptools import setup
except ImportError:
   from distutils.core import setup

setup(name='floamtv',
      url='http://aarongyes.com/stuff/floamtv',
      version='0.24',
      py_modules=['fuzzydict'],
      scripts=['floamtv.py'],
      author='Aaron Gyes',
      author_email='floam@sh.nu',
      install_requires=['simplejson'],
)