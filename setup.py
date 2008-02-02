#!/usr/bin/env python

from setuptools import setup

setup(name='floamtv',
      url='http://aarongyes.com/stuff/floamtv',
      version='0.2bzr',
      py_modules=['fuzzydict'],
      scripts=['floamtv.py'],
      author='Aaron Gyes',
      author_email='floam@sh.nu',
      install_requires=['simplejson'],
)