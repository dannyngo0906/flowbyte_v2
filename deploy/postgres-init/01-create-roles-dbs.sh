#!/bin/bash
# Postgres init script — runs once when haravan_pg_data volume is empty.
# Creates Metabase roles + metabase_app database.
# Reads passwords from container environment (set via stack .env).

set -e
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d haravan <<-SQL
  CREATE ROLE metabase_app    LOGIN PASSWORD '${METABASE_APP_PASSWORD}';
  CREATE ROLE metabase_reader LOGIN PASSWORD '${METABASE_READER_PASSWORD}';
  CREATE DATABASE metabase_app OWNER metabase_app;
SQL
