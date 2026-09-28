#!/bin/sh
set -eu
exec 9>/opt/tour-agent/public/certificate-renewal.lock
flock -n 9 || exit 0
docker run --rm --network host \
  -v /var/lib/docker/volumes/deploy_caddy_data/_data/tour-letsencrypt:/etc/letsencrypt \
  -v /var/lib/docker/volumes/deploy_caddy_data/_data/tour-certbot-work:/var/lib/letsencrypt \
  -v /var/lib/docker/volumes/deploy_caddy_data/_data/tour-certbot-logs:/var/log/letsencrypt \
  -v /var/lib/docker/volumes/deploy_caddy_data/_data/tour-acme:/var/www/acme \
  docker.m.daocloud.io/certbot/certbot@sha256:78a7eaa40af657301e25731297b6e73646c695370d17e9bb03189fa82966d65c \
  renew --cert-name tour-ip --non-interactive --quiet
docker exec deploy-caddy-1 caddy reload --config /etc/caddy/Caddyfile --force
