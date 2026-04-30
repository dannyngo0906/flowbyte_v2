#!/usr/bin/env bash
# Bootstrap haravan-elt production stack on a fresh Ubuntu VPS.
#
# Idempotent. Run as user with sudo. Reads HARAVAN_*, TELEGRAM_*, and
# METABASE_SITE_URL from caller environment (or prompts if missing).
#
# Layout after run:
#   /opt/haravan-elt-stack/             docker compose stack (postgres + metabase)
#     ├── docker-compose.yml            symlink → repo deploy/docker-compose.prod.yml
#     ├── postgres-init/                symlink → repo deploy/postgres-init/
#     └── .env                          generated random passwords (root:root 600)
#   /opt/haravan-elt/                   project (this repo) — owner elt:elt
#   /etc/systemd/system/haravan-elt.{service,timer}
#   /etc/cron.d/haravan-elt             daily backup + monthly cleanup
#
# Usage on a fresh VPS:
#   git clone https://github.com/dannyngo0906/flowbyte_v2.git /tmp/haravan-elt
#   cd /tmp/haravan-elt
#   export HARAVAN_SHOP_DOMAIN=...
#   export HARAVAN_CLIENT_ID=...
#   export HARAVAN_CLIENT_SECRET=...
#   export HARAVAN_ACCESS_TOKEN=...
#   export HARAVAN_REFRESH_TOKEN=...
#   export TELEGRAM_BOT_TOKEN=...
#   export TELEGRAM_CHAT_ID=...
#   export METABASE_SITE_URL=https://metabase.example.com  # optional
#   sudo -E bash deploy/install-prod-stack.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR=/opt/haravan-elt
STACK_DIR=/opt/haravan-elt-stack

require_var() {
  local name=$1
  if [ -z "${!name:-}" ]; then
    echo "ERROR: \$$name is required (export it before running)" >&2
    exit 1
  fi
}

# ---------------------------------------------------------------- prereqs ---
echo "==> [1/8] Checking required env vars"
for v in HARAVAN_SHOP_DOMAIN HARAVAN_CLIENT_ID HARAVAN_CLIENT_SECRET \
         HARAVAN_ACCESS_TOKEN HARAVAN_REFRESH_TOKEN; do
  require_var "$v"
done
: "${TELEGRAM_BOT_TOKEN:=}"
: "${TELEGRAM_CHAT_ID:=}"
: "${METABASE_SITE_URL:=http://localhost:3001}"
: "${METABASE_SITE_NAME:=Haravan ELT}"

echo "==> [2/8] Installing apt packages (python3.11, pg-client-16, docker check)"
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg software-properties-common \
                       build-essential libpq-dev sqlite3
add-apt-repository -y ppa:deadsnakes/ppa
apt-get install -y -qq python3.11 python3.11-venv python3.11-dev

# Postgres apt repo for client v16
if ! command -v psql >/dev/null || ! psql --version 2>/dev/null | grep -q "16\."; then
  install -d /usr/share/postgresql-common/pgdg
  curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
  CODENAME=$(. /etc/os-release && echo "$VERSION_CODENAME")
  echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
https://apt.postgresql.org/pub/repos/apt ${CODENAME}-pgdg main" \
    > /etc/apt/sources.list.d/pgdg.list
  apt-get update -qq
  apt-get install -y -qq postgresql-client-16
fi

if ! command -v docker >/dev/null; then
  echo "ERROR: Docker not installed. Install Docker Engine first." >&2
  exit 1
fi

# ----------------------------------------------------------------- user ---
echo "==> [3/8] Creating elt user"
id elt >/dev/null 2>&1 || useradd -m -s /bin/bash elt

# ------------------------------------------------------------- project ---
echo "==> [4/8] Installing project at $APP_DIR"
mkdir -p "$APP_DIR"
rsync -a --delete --exclude=.venv --exclude=.git --exclude=dbt/target \
  --exclude=dbt/dbt_packages --exclude=dbt/logs \
  "$REPO_DIR/" "$APP_DIR/"
chown -R elt:elt "$APP_DIR"

# venv + pip install -e .
sudo -u elt -i bash -c "
  cd $APP_DIR
  python3.11 -m venv .venv
  source .venv/bin/activate
  pip install --upgrade pip --quiet
  pip install -e . --quiet
"

# .env (only if missing — never overwrite user's secrets)
if [ ! -f "$APP_DIR/.env" ]; then
  ELT_PASS_PLACEHOLDER='__SET_BY_install-prod-stack__'
  cat > "$APP_DIR/.env" <<EOF
DATABASE_URL=postgresql://elt_user:${ELT_PASS_PLACEHOLDER}@localhost:5433/haravan
HARAVAN_SHOP_DOMAIN=${HARAVAN_SHOP_DOMAIN}
HARAVAN_CLIENT_ID=${HARAVAN_CLIENT_ID}
HARAVAN_CLIENT_SECRET=${HARAVAN_CLIENT_SECRET}
HARAVAN_ACCESS_TOKEN=${HARAVAN_ACCESS_TOKEN}
HARAVAN_REFRESH_TOKEN=${HARAVAN_REFRESH_TOKEN}
HARAVAN_RATE_LIMIT_PER_SEC=4
HARAVAN_RATE_LIMIT_BURST=80
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID}
LOG_LEVEL=INFO
LOG_FORMAT=json
TZ=Asia/Ho_Chi_Minh
EOF
  chmod 600 "$APP_DIR/.env"
  chown elt:elt "$APP_DIR/.env"
fi

# ------------------------------------------------------------- stack ---
echo "==> [5/8] Setting up docker compose stack at $STACK_DIR"
mkdir -p "$STACK_DIR"
ln -sfn "$APP_DIR/deploy/docker-compose.prod.yml" "$STACK_DIR/docker-compose.yml"
ln -sfn "$APP_DIR/deploy/postgres-init"            "$STACK_DIR/postgres-init"

# Generate stack .env with random passwords (idempotent — only if absent)
if [ ! -f "$STACK_DIR/.env" ]; then
  ELT_USER_PASSWORD=$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)
  METABASE_APP_PASSWORD=$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)
  METABASE_READER_PASSWORD=$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)
  cat > "$STACK_DIR/.env" <<EOF
ELT_USER_PASSWORD=${ELT_USER_PASSWORD}
METABASE_APP_PASSWORD=${METABASE_APP_PASSWORD}
METABASE_READER_PASSWORD=${METABASE_READER_PASSWORD}
METABASE_SITE_URL=${METABASE_SITE_URL}
METABASE_SITE_NAME=${METABASE_SITE_NAME}
EOF
  chmod 600 "$STACK_DIR/.env"
  # Sync DATABASE_URL in app .env with real password
  sed -i "s|__SET_BY_install-prod-stack__|${ELT_USER_PASSWORD}|" "$APP_DIR/.env"
fi

# ----------------------------------------------------------- containers ---
echo "==> [6/8] Starting containers"
docker compose -f "$STACK_DIR/docker-compose.yml" --env-file "$STACK_DIR/.env" up -d
docker compose -f "$STACK_DIR/docker-compose.yml" wait postgres 2>/dev/null || true
sleep 5

# --------------------------------------------------------- dbt profile ---
echo "==> [7/8] Writing dbt profile + running dbt deps"
ELT_PASS=$(grep '^ELT_USER_PASSWORD=' "$STACK_DIR/.env" | cut -d= -f2)
mkdir -p /home/elt/.dbt
cat > /home/elt/.dbt/profiles.yml <<EOF
haravan_elt:
  target: prod
  outputs:
    prod:
      type: postgres
      host: localhost
      port: 5433
      user: elt_user
      password: ${ELT_PASS}
      dbname: haravan
      schema: staging
      threads: 4
EOF
chmod 700 /home/elt/.dbt
chmod 600 /home/elt/.dbt/profiles.yml
chown -R elt:elt /home/elt/.dbt

sudo -u elt -i bash -c "
  cd $APP_DIR/dbt
  source ../.venv/bin/activate
  dbt deps
"

# --------------------------------------------------------- systemd + cron ---
echo "==> [8/8] Installing systemd timer + cron jobs"
cp "$APP_DIR/deploy/systemd/haravan-elt.service" /etc/systemd/system/
cp "$APP_DIR/deploy/systemd/haravan-elt.timer"   /etc/systemd/system/
# Timer template already declares Asia/Ho_Chi_Minh on each OnCalendar — no patch needed.

cp "$APP_DIR/deploy/crontab.example" /etc/cron.d/haravan-elt
# Adjust paths in cron file (already absolute, no-op) and ensure newline-terminated
echo "" >> /etc/cron.d/haravan-elt
chown root:root /etc/cron.d/haravan-elt
chmod 644 /etc/cron.d/haravan-elt

touch /var/log/haravan-elt.log /var/log/haravan-elt-cleanup.log /var/log/haravan-elt-backup.log
chown elt:elt /var/log/haravan-elt*.log

systemctl daemon-reload
systemctl enable haravan-elt.timer

echo
echo "============================================================"
echo " ✅ Bootstrap complete."
echo "============================================================"
echo "Next steps:"
echo "  1. Run a first backfill (long):"
echo "       sudo -u elt -i bash -c 'cd $APP_DIR && source .venv/bin/activate && set -a; source .env; set +a; haravan-elt init && haravan-elt run-all --mode full --no-notify'"
echo "  2. Verify Metabase at http://<vps>:3001 (or behind your reverse proxy → \$METABASE_SITE_URL)"
echo "  3. Start timer: sudo systemctl start haravan-elt.timer"
echo "  4. Check timer schedule: systemctl list-timers haravan-elt.timer"
