#!/bin/sh

mkdir release
cp -X floamtv.py release
cp -X floamtvconfig2 release
cp -X LICENSE release
cp -X README release

sed -e s/"internal"/`bzr revno`/ floamtv.py > release/floamtv.py

cd release
tar cvvf ../floamtv-`bzr revno`.tar *
gzip ../floamtv*.tar
cd ..

rm -r release