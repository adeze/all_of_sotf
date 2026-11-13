FROM buildbot/buildbot-worker:v4.1.0

USER root

# Retry flaky Debian mirror downloads during the build environment
RUN echo 'Acquire::Retries "3";' > /etc/apt/apt.conf.d/99-retries

# System dependencies for the Rust workspaces
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    build-essential \
    pkg-config \
    cmake \
    ninja-build \
    perl \
    clang \
    libclang-dev \
    libssl-dev \
    libsqlite3-dev \
    libglib2.0-dev \
    libxkbcommon-dev \
    libxkbcommon-x11-dev \
    libgtk-3-dev \
    libwebkit2gtk-4.1-dev \
    libayatana-appindicator3-dev \
    librsvg2-dev \
    libasound2-dev \
    libudev-dev \
    libv4l-dev \
    libfontconfig1-dev \
    libx11-dev \
    libxcb1-dev \
    libxcb-render0-dev \
    libxcb-shape0-dev \
    libxcb-xfixes0-dev \
    libxi-dev \
    libxcursor-dev \
    libxrandr-dev \
    libopenblas-dev \
    gfortran \
    patchelf \
    xvfb \
    git \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Install rustup and the stable toolchain as the buildbot user so the
# resulting Cargo tools are accessible to the worker runtime.
USER buildbot
ENV RUSTUP_MAX_RETRIES=10
RUN curl --http1.1 --proto '=https' --tlsv1.2 -fsSL \
        --continue-at - --retry 20 --retry-delay 2 --retry-all-errors \
        -o /tmp/rustup-init \
        https://static.rust-lang.org/rustup/dist/x86_64-unknown-linux-gnu/rustup-init && \
    chmod +x /tmp/rustup-init && \
    /tmp/rustup-init -y --default-toolchain stable --profile minimal && \
    rm /tmp/rustup-init
ENV PATH="/home/buildbot/.cargo/bin:${PATH}"

# The repository's canonical test and coverage recipes use these Cargo tools.
RUN cargo install cargo-nextest && \
    cargo install cargo-llvm-cov && \
    cargo install just --version 1.40.0

# Named volumes are created as root by Docker; pre-create and chown the
# directories so the buildbot user can write the Cargo cache and target cache.
USER root
RUN mkdir -p /home/buildbot/.cargo/registry /home/buildbot/.cargo/git /workspace/target \
    && chown -R buildbot:buildbot /home/buildbot/.cargo /workspace/target

USER buildbot
