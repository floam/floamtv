#!/bin/sh

mkdir release
cp -X floamtv.py release
cp -X floamtvconfig2 release
cp -X LICENSE release

sed -e s/"internal"/`bzr revno`/ floamtv.py > release/floamtv.py

tar -cvvfz -C release release.tar.gz
rm -r release