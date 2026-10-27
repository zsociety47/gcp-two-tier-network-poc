#!/bin/bash
# Install PostgreSQL and listen on the private address only.
# The password arrives through psql's \getenv, not on the command line.

set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y postgresql python3

conf_dir="$(ls -d /etc/postgresql/*/main | head -n 1)"
sed -i "s/^#\\?listen_addresses.*/listen_addresses = '__DB_ADDRESS__'/" "${conf_dir}/postgresql.conf"
grep -q "__DB_ADDRESS__" "${conf_dir}/postgresql.conf"

hba="${conf_dir}/pg_hba.conf"
if ! grep -q "appdb webapp __WEB_PREFIX__" "${hba}"; then
  printf 'host appdb webapp __WEB_PREFIX__ scram-sha-256\n' >> "${hba}"
fi
systemctl restart postgresql

export TWOTIER_DB_PASSWORD
TWOTIER_DB_PASSWORD="$(fetch_secret)"

role_exists="$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='webapp'")"
if [[ "${role_exists}" != "1" ]]; then
  sudo --preserve-env=TWOTIER_DB_PASSWORD -u postgres psql -v ON_ERROR_STOP=1 <<'SQL'
\getenv pw TWOTIER_DB_PASSWORD
CREATE ROLE webapp LOGIN PASSWORD :'pw';
SQL
fi

db_exists="$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='appdb'")"
if [[ "${db_exists}" != "1" ]]; then
  sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE DATABASE appdb OWNER webapp;"
fi

unset TWOTIER_DB_PASSWORD
report_ready "DB_READY"
