# Dockerfile
FROM ubuntu:latest

# install stress-ng
RUN apt-get update && \
    apt-get install -y stress-ng && \
    rm -rf /var/lib/apt/lists/*

# keep the container running
CMD ["tail", "-f", "/dev/null"]