# syntax=docker/dockerfile:1.7
FROM rust:slim-bookworm@sha256:f2c004e3d2d8b547e8cf7ffbc29205385f4e71d23e53e4e383065db5e9f822cd AS build
RUN apt-get update && apt-get install -y --no-install-recommends python3 pkg-config cmake clang lld make ca-certificates git libssl-dev \
    && rm -rf /var/lib/apt/lists/*
COPY upstream.tar.gz /tmp/upstream.tar.gz
RUN mkdir /src && tar -xzf /tmp/upstream.tar.gz --strip-components=1 -C /src && rm /tmp/upstream.tar.gz
COPY prepare-build.py /tmp/prepare-build.py
WORKDIR /src
RUN python3 /tmp/prepare-build.py
ENV CARGO_BUILD_JOBS=1 CARGO_PROFILE_DEV_DEBUG=0 CARGO_INCREMENTAL=0
ENV RUSTFLAGS="-C link-arg=-fuse-ld=lld"
ENV EMULATOR_VERSION=1.0.322-local-ec2fix EMULATOR_COMMIT=7753983c6e495ec3612695b1336bf4936fc7b7fd+local-ec2fix
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    rustc --version && cargo build --locked -p aws-emulator-router -j 1 \
    && cp target/debug/aws-emulator /out/aws-emulator \
    && sha256sum /out/aws-emulator > /out/binary.sha256

FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl libssl3 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home emulator
COPY --from=build /out/aws-emulator /usr/local/bin/aws-emulator
COPY --from=build /out/build-manifest.json /out/ec2-fixes.patch /out/build-service-selection.patch /out/binary.sha256 /opt/emulator-build/
USER 1000
ENV HOME=/home/emulator EMULATOR_TELEMETRY=off
ENTRYPOINT ["aws-emulator", "-b", "0.0.0.0", "-p", "19399", "--telemetry", "off"]
