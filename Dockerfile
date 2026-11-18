FROM golang:1.26.1-bookworm@sha256:09fb8a652cf7a990b714c46a9f0f5fd2d5bc2222d995166b91907c1c05b7d0e8 AS build

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    go build -trimpath -buildvcs=false -ldflags="-s -w" \
    -o /out/netvue-bridge ./cmd/netvue-bridge

FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc
RUN rm -f /etc/apt/sources.list.d/debian.sources \
    && printf '%s\n' \
      'deb http://snapshot.debian.org/archive/debian/20261105T000000Z bookworm main' \
      'deb http://snapshot.debian.org/archive/debian-security/20261105T000000Z bookworm-security main' \
      > /etc/apt/sources.list \
    && apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /out/netvue-bridge /netvue-bridge
LABEL org.opencontainers.image.source="https://github.com/mtx26/netvue-ha"
USER 10001:10001
STOPSIGNAL SIGTERM
ENTRYPOINT ["/netvue-bridge"]
