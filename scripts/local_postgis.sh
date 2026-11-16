#!/bin/sh
# Start a private PostGIS server for this project in .pgdata (port 5433, socket only),
# create the schema on first run, and print the MAP_AGENT_DATABASE_URL to use.
# Uses Postgres.app's binaries; set PG_BIN to use another install.
set -eu
cd "$(dirname "$0")/.."
BIN=${PG_BIN:-/Applications/Postgres.app/Contents/Versions/latest/bin}
DATA=$PWD/.pgdata
PORT=5433
URL="postgresql:///map_agent?host=$DATA&port=$PORT"
if [ ! -f "$DATA/PG_VERSION" ]; then
    "$BIN/initdb" -D "$DATA" -U "$(whoami)" --auth-local=peer --auth-host=scram-sha-256 -E UTF8 >/dev/null
fi
if ! "$BIN/pg_ctl" -D "$DATA" status >/dev/null 2>&1; then
    "$BIN/pg_ctl" -D "$DATA" -o "-p $PORT -k $DATA -c listen_addresses=''" -l "$DATA/server.log" -w start >/dev/null
fi
if ! "$BIN/psql" "$URL" -Atc "SELECT 1" >/dev/null 2>&1; then
    "$BIN/createdb" -h "$DATA" -p "$PORT" map_agent
    "$BIN/psql" "$URL" -q -v ON_ERROR_STOP=1 -c "CREATE EXTENSION postgis" -c "CREATE EXTENSION pg_trgm" -f sql/001_schema.sql
fi
# Apply later migrations that this database has not seen yet.
if [ "$("$BIN/psql" "$URL" -Atc "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'map_agent' AND table_name = 'places' AND column_name = 'county'")" = 0 ]; then
    "$BIN/psql" "$URL" -q -v ON_ERROR_STOP=1 -f sql/002_place_county.sql
fi
echo "export MAP_AGENT_DATABASE_URL=\"$URL\""
