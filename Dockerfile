FROM golang:1.26.1-bookworm@sha256:09fb8a652cf7a990b714c46a9f0f5fd2d5bc2222d995166b91907c1c05b7d0e8 AS build

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    go build -trimpath -buildvcs=false -ldflags="-s -w" \
    -o /out/netvue-bridge ./cmd/netvue-bridge

FROM scratch
COPY --from=build /out/netvue-bridge /netvue-bridge
LABEL org.opencontainers.image.source="https://github.com/mtx26/netvue-ha"
USER 10001:10001
STOPSIGNAL SIGTERM
ENTRYPOINT ["/netvue-bridge"]
