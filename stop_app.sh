#!/bin/bash

hosts=("host1" "host2" "host3")

for host in "${hosts[@]}"; do
  echo "Stopping and removing containers for $host..."
  cd "$host"
  docker-compose down
  cd ..
done