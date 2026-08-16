#!/bin/bash

rm -rf dist/
rm -rf ris-musl

docker build -t rissup-alpine -f Dockerfile.alpine .
docker create --name temp-alp rissup-alpine
docker cp temp-alp:/app/rissup ./ris-musl
docker rm temp-alp