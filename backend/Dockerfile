# Field Desk API (Go). Multi-stage: build a static binary, ship it on alpine.
# Built with context: ../backend from ops/docker-compose.yml.
FROM golang:1.25-alpine AS builder
WORKDIR /app
RUN apk add --no-cache git
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /app/agf-api .

FROM alpine:3.20
RUN apk add --no-cache ca-certificates wget && adduser -D -u 10001 app
WORKDIR /app
COPY --from=builder /app/agf-api /app/agf-api
USER app
EXPOSE 8000
# Container healthcheck mirrors the compose-level api check.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8000/healthz || exit 1
ENTRYPOINT ["/app/agf-api"]
