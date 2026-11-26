#!/bin/sh
# Runs from the nginx image entrypoint (/docker-entrypoint.d) before nginx starts. Writes the
# `resolver` directive that deploy/nginx/default.conf includes, using the nameservers of the
# container's own /etc/resolv.conf: 127.0.0.11 under Docker, the network gateway under Podman.
# `valid=10s` makes nginx look the api name up again soon after the container is recreated.
set -eu
servers=$(awk '/^nameserver/ { print ($2 ~ /:/ ? "[" $2 "]" : $2) }' /etc/resolv.conf | tr '\n' ' ')
[ -n "$servers" ] || servers="127.0.0.11"
echo "resolver ${servers} valid=10s ipv6=off;" > /tmp/nginx-resolver.conf
echo "nginx-resolver: resolver ${servers}"
