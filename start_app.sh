#!/bin/bash

# host dirs
hosts=("host1" "host2" "host3")

# run docker-compose up for each host
for host in "${hosts[@]}"; do
  echo "Starting containers for $host..."
  cd "$host"
  docker-compose up -d
  cd ..
done
