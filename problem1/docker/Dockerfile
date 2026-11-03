FROM ghcr.io/gem5/ubuntu-24.04_all-dependencies:v25-1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        g++-multilib \
        python3-matplotlib \
        python3-yaml \
    && rm -rf /var/lib/apt/lists/*
