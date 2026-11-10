#!/usr/bin/env bash
# One-time setup of YouTube Downloader on a fresh Ubuntu 24.04 server
# (made for Oracle Cloud's Always Free tier). Run it again any time to change the password.
#
#   sudo bash setup.sh
#
# Optional: DOMAIN=your.domain sudo -E bash setup.sh   (defaults to a free <ip>.sslip.io address)
set -euo pipefail

REPO_URL="https://github.com/sorovistworks/ytdownloader.git"
APP_DIR="/opt/ytdownloader"
DEPLOY_DIR="$APP_DIR/deploy/oracle"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf '\n\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run this with sudo:  sudo bash $0"

# ---------------------------------------------------------------- firewall
say "1/6  Opening web ports 80 and 443 on this server's firewall"
# Oracle's Ubuntu images block everything except SSH. Allow web traffic now...
for port in 443 80; do
  if ! iptables -C INPUT -p tcp -m state --state NEW -m tcp --dport "$port" -j ACCEPT 2>/dev/null; then
    pos=$(iptables -L INPUT --line-numbers -n | awk '$2=="REJECT" {print $1; exit}')
    if [ -n "$pos" ]; then
      iptables -I INPUT "$pos" -p tcp -m state --state NEW -m tcp --dport "$port" -j ACCEPT
    else
      iptables -A INPUT -p tcp -m state --state NEW -m tcp --dport "$port" -j ACCEPT
    fi
  fi
done
# ...and after reboots. Only this one file is edited, so Docker's own rules are never saved into it.
RULES=/etc/iptables/rules.v4
if [ -f "$RULES" ] && ! grep -q -- "--dport 443 -j ACCEPT" "$RULES"; then
  sed -i '/^-A INPUT -j REJECT/i -A INPUT -p tcp -m state --state NEW -m tcp --dport 80 -j ACCEPT\n-A INPUT -p tcp -m state --state NEW -m tcp --dport 443 -j ACCEPT' "$RULES"
fi

# ---------------------------------------------------------------- packages
say "2/6  Installing Docker (a few minutes)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q docker.io docker-compose-v2 git curl openssl \
  || die "Installing Docker failed. Make sure the server uses the Ubuntu 24.04 image."
systemctl enable --now docker >/dev/null

# ---------------------------------------------------------------- code
say "3/6  Downloading the app"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull -q
else
  git clone -q "$REPO_URL" "$APP_DIR"
fi
mkdir -p "$DEPLOY_DIR/secrets"
chmod 700 "$DEPLOY_DIR/secrets"

# ---------------------------------------------------------------- settings
say "4/6  Settings"
ENV_FILE="$DEPLOY_DIR/.env"
old() { [ -f "$ENV_FILE" ] && sed -n "s/^$1='\(.*\)'$/\1/p" "$ENV_FILE" | head -n1 || true; }

IP=$(curl -fsS --max-time 10 https://api.ipify.org || curl -fsS --max-time 10 https://ifconfig.me || true)
[ -n "${DOMAIN:-}" ] || [ -n "$IP" ] || die "Couldn't find this server's public IP address. Run again with DOMAIN=... set."
DOMAIN="${DOMAIN:-${IP//./-}.sslip.io}"

SECRET=$(old SECRET_KEY)
[ -n "$SECRET" ] || SECRET=$(openssl rand -hex 32)

while true; do
  read -r -s -p "Choose the family password (leave empty for no password): " PW </dev/tty; echo
  if [ -z "$PW" ]; then
    read -r -p "No password means anyone with the link can use it. Continue? [y/N] " yn </dev/tty
    [[ "$yn" =~ ^[Yy] ]] && break || continue
  fi
  case "$PW" in *"'"*) echo "Please don't use the ' character."; continue ;; esac
  read -r -s -p "Type it again: " PW2 </dev/tty; echo
  [ "$PW" = "$PW2" ] && break
  echo "They don't match. Try again."
done

umask 077
cat > "$ENV_FILE" <<EOF
SITE_DOMAIN='$DOMAIN'
APP_PASSWORD='$PW'
SECRET_KEY='$SECRET'
EOF
umask 022

# ---------------------------------------------------------------- start
say "5/6  Building and starting (the first time takes 5-10 minutes)"
cd "$DEPLOY_DIR"
YTDLP_STAMP=$(date +%F) docker compose up -d --build

# ---------------------------------------------------------------- updates
say "6/6  Turning on nightly updates"
cat > /etc/cron.d/ytdownloader <<EOF
# Every night at 04:30 server time: get the latest app and the newest yt-dlp, then restart.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
30 4 * * * root cd $DEPLOY_DIR && git -C $APP_DIR pull -q && YTDLP_STAMP=\$(date +\%F) docker compose up -d --build > /var/log/ytdownloader-update.log 2>&1 && docker image prune -f > /dev/null
EOF
chmod 644 /etc/cron.d/ytdownloader

# ---------------------------------------------------------------- check
say "Waiting for the site to come online and get its https certificate..."
for _ in $(seq 1 60); do
  if curl -fsS --max-time 5 "https://$DOMAIN/api/health" >/dev/null 2>&1; then
    say "Done! Your family's link:  https://$DOMAIN"
    [ -n "$PW" ] && echo "Password: the one you just chose."
    exit 0
  fi
  sleep 5
done
die "The site didn't answer at https://$DOMAIN yet.
Check that ports 80 and 443 are open in Oracle's Security List (see the guide, step 4),
then run:  cd $DEPLOY_DIR && sudo docker compose logs --tail 50"
