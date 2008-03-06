#!/usr/bin/env python

try:
   from setuptools import setup
except ImportError:
   from distutils.core import setup

setup(name='floamtv',
      url='http://aaron.gy/stuff/floamtv',
      version='0.3',
      author='Aaron Gyes',
      author_email='floam@sh.nu',
      install_requires=['pyyaml'],
)