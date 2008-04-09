#!/bin/sh

NAME=floamtv-`bzr revno`

mkdir ${NAME}
cp -X floamtv.py ${NAME}
cp -X floamtvconfig2 ${NAME}
cp -X LICENSE ${NAME}
cp -X README ${NAME}

sed -e s/"internal"/`bzr revno`/ floamtv.py > ${NAME}/floamtv.py

tar cvvf ${NAME}.tar ${NAME}
gzip ${NAME}.tar

rm -r ${NAME}