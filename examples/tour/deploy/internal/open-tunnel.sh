#!/bin/sh
set -eu
exec ssh -N \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=yes \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -i /Users/k/.ssh/ai_travel_ecs \
  -L 127.0.0.1:18080:127.0.0.1:18080 \
  root@8.130.118.212
