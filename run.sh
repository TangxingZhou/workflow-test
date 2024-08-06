#!/bin/bash

set -e

echo "shell args" "$@"

if [ $# -lt 6 ]; then
    echo "not enough args, exit 1"
    exit 1
fi

backupEnv=$1
moCluster=$2
moBrImage=$3
ossBucket=$4
ossFilePath=$5
parallelism=$6
baseID=$7


echo "new backupID: 123456-789-asd"
