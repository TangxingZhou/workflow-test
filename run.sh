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

# echo "shell args prod freetier-01 registry.cn-hangzhou.aliyuncs.com/mocloud/mo-backup:main-20240731 mo-backup-20240201 backup0717-1520 150"
echo "new backupID: 019125ba-6147-7d41-a430-84236dadf806"
echo "backup-id=123456-789-asd" >> $GITHUB_OUTPUT
echo "backup-id=123456-789-asd" >> $GITHUB_ENV
