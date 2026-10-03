#!/usr/bin/env bash
# Disposable single-node MongoDB replica set for docs/demo.md (TLS + auth, like production).
#   docs/demo/mongo.sh up DIR     start it; writes DIR/ca.pem and DIR/env.sh
#   docs/demo/mongo.sh down DIR   remove the container and DIR
# Port 27118, loopback only; never the default 27017. Not for production: see
# docs/operations/replica-set.md for the real three-host recipe.
set -euo pipefail
cmd="${1:?usage: mongo.sh up|down DIR}"; dir="${2:?usage: mongo.sh up|down DIR}"
name=culture-rules-demo-mongo; port=27118
if [ "$cmd" = down ]; then docker rm -f "$name" >/dev/null 2>&1 || true; rm -rf "$dir"; exit 0; fi
mkdir -p "$dir"; cd "$dir"
printf 'subjectAltName=DNS:localhost,IP:127.0.0.1\n' > ext.cnf
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj /CN=demo-ca -keyout ca.key -out ca.pem 2>/dev/null
openssl req -newkey rsa:2048 -nodes -subj /CN=localhost -keyout s.key -out s.csr 2>/dev/null
openssl x509 -req -in s.csr -CA ca.pem -CAkey ca.key -CAcreateserial -days 2 -extfile ext.cnf -out s.crt 2>/dev/null
cat s.crt s.key > server.pem; openssl rand -base64 256 > keyfile; chmod 644 ./*
docker rm -f "$name" >/dev/null 2>&1 || true
docker run -d --rm --network host --name "$name" -v "$PWD:/certs:ro" --entrypoint bash mongo:8.0 -c \
  "mkdir -p /tmp/db && cp /certs/keyfile /tmp/keyfile && chmod 400 /tmp/keyfile && exec mongod \
--replSet rs0 --bind_ip 127.0.0.1 --port $port --keyFile /tmp/keyfile --tlsMode requireTLS \
--tlsCertificateKeyFile /certs/server.pem --tlsCAFile /certs/ca.pem \
--tlsAllowConnectionsWithoutCertificates --dbpath /tmp/db --wiredTigerCacheSizeGB 0.25" >/dev/null
m() { docker exec "$name" mongosh --quiet --port $port --tls --tlsCAFile /certs/ca.pem \
  --tlsAllowInvalidHostnames "$@"; }
for _ in $(seq 60); do
  m --eval 'rs.initiate({_id:"rs0",members:[{_id:0,host:"127.0.0.1:'$port'"}]}).ok' \
    >/dev/null 2>&1 && break; sleep 1; done
for _ in $(seq 60); do
  [ "$(m --eval 'db.hello().isWritablePrimary' 2>/dev/null)" = true ] && break; sleep 1; done
m --eval 'db.getSiblingDB("admin").createUser({user:"demo-admin",pwd:"demo-admin-pw",roles:["root"]})' >/dev/null
m -u demo-admin -p demo-admin-pw --authenticationDatabase admin --eval \
  'db.getSiblingDB("culture_rules").createUser({user:"demo-app",pwd:"demo-app-pw",roles:[{role:"readWrite",db:"culture_rules"}]})' >/dev/null
cat > env.sh <<ENV
export CULTURE_RULES_MONGO_URI='mongodb://demo-app:demo-app-pw@127.0.0.1:$port/culture_rules?replicaSet=rs0&authSource=culture_rules'
export CULTURE_RULES_MONGO_TLS_CA_FILE='$PWD/ca.pem'
export CULTURE_RULES_API_URL='http://127.0.0.1:8791'
ENV
echo "mongo up on 127.0.0.1:$port; source $PWD/env.sh"
