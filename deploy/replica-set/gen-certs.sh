#!/usr/bin/env bash
# Generate the shared secrets for the replica set, ONCE, on a trusted machine:
#   ./gen-certs.sh OUT_DIR HOST [HOST...]
# HOST is a DNS name or IP each member is reached by (tailnet name or address).
# Writes OUT_DIR/keyfile (intra-set auth), ca.pem/ca.key (TLS CA), and one
# OUT_DIR/<host>/server.pem per host (cert+key, SAN = that host). Copy keyfile, ca.pem and
# that host's server.pem to /etc/culture-rules-mongo on the host. Never commit OUT_DIR.
# Membership auth is the keyFile. For x509 membership instead, add
# `--clusterAuthMode x509` and issue server.pem with the same CA (see docs/operations).
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "usage: $0 OUT_DIR HOST [HOST...]" >&2
  exit 1
fi
out="$1"; shift
mkdir -p "$out"
umask 077

[ -f "$out/keyfile" ] || openssl rand -base64 756 > "$out/keyfile"
if [ ! -f "$out/ca.pem" ]; then
  openssl req -x509 -newkey rsa:4096 -nodes -days 3650 \
    -subj "/CN=culture-rules-mongo-ca" -keyout "$out/ca.key" -out "$out/ca.pem"
fi

for host in "$@"; do
  dir="$out/$host"
  mkdir -p "$dir"
  if [[ "$host" =~ ^[0-9.]+$ ]]; then san="IP:$host,DNS:localhost"; else san="DNS:$host,DNS:localhost"; fi
  printf 'subjectAltName=%s\n' "$san" > "$dir/ext.cnf"
  openssl req -newkey rsa:4096 -nodes -subj "/CN=$host" -keyout "$dir/s.key" -out "$dir/s.csr"
  openssl x509 -req -in "$dir/s.csr" -CA "$out/ca.pem" -CAkey "$out/ca.key" -CAcreateserial \
    -days 825 -extfile "$dir/ext.cnf" -out "$dir/s.crt"
  cat "$dir/s.crt" "$dir/s.key" > "$dir/server.pem"
  rm -f "$dir/s.csr" "$dir/s.crt" "$dir/s.key" "$dir/ext.cnf"
done
echo "wrote secrets to $out"
