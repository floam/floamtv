#!/bin/sh

NAME=floamtv-`bzr revno`

mkdir ${NAME}
cp -X floamtv.py ${NAME}
cp -X floamtvconfig2 ${NAME}
cp -X LICENSE ${NAME}
tail -n +4 ~/Sites/floamshnu/cms/stuff/floamtv.txt > ${NAME}/README

sed -e s/"internal"/`bzr revno`/ floamtv.py > ${NAME}/floamtv.py

tar cvvf ${NAME}.tar ${NAME}
gzip ${NAME}.tar
mv ${NAME}.tar.gz ~/Sites/floamshnu/static

rm -r ${NAME}