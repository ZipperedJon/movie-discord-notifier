#!/usr/bin/env bash
#
# Movie Discord Notifier — installer for Linux / Raspberry Pi.
#
#   curl -fsSL https://raw.githubusercontent.com/ZipperedJon/movie-discord-notifier/main/install.sh | sudo bash
#
# Installs to /opt/movie-discord-notifier, runs it as a locked-down system user
# under systemd, and starts it on boot.
#
#   sudo ./install.sh --update      pull the latest code and restart
#   sudo ./install.sh --uninstall   remove the service, keep data/app.db
#   sudo ./install.sh --uninstall --purge   remove everything, data included
#
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/ZipperedJon/movie-discord-notifier.git}"
BRANCH="${BRANCH:-main}"
APP_DIR="${APP_DIR:-/opt/movie-discord-notifier}"
SERVICE="${SERVICE:-movie-notifier}"
RUN_USER="${RUN_USER:-movienotifier}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

usage() {
  cat <<'USAGE'
Movie Discord Notifier — installer for Linux / Raspberry Pi.

  sudo ./install.sh                    install and start on boot
  sudo ./install.sh --update           pull the latest code and restart
  sudo ./install.sh --uninstall        remove the service, keep your data
  sudo ./install.sh --uninstall --purge   remove everything, data included

  --host=ADDR   what to listen on (default 0.0.0.0, the whole LAN)
  --port=N      port to serve on  (default 8000)
USAGE
}

MODE="install"
PURGE="no"
for arg in "$@"; do
  case "$arg" in
    --update)    MODE="update" ;;
    --uninstall) MODE="uninstall" ;;
    --purge)     PURGE="yes" ;;
    --host=*)    HOST="${arg#*=}" ;;
    --port=*)    PORT="${arg#*=}" ;;
    -h|--help)   usage; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

BOLD=$(tput bold 2>/dev/null || true)
DIM=$(tput dim 2>/dev/null || true)
GREEN=$(tput setaf 2 2>/dev/null || true)
YELLOW=$(tput setaf 3 2>/dev/null || true)
RED=$(tput setaf 1 2>/dev/null || true)
RESET=$(tput sgr0 2>/dev/null || true)

say()  { echo "${GREEN}==>${RESET} ${BOLD}$*${RESET}"; }
warn() { echo "${YELLOW}==> $*${RESET}"; }
die()  { echo "${RED}==> error: $*${RESET}" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root:  curl -fsSL <url> | sudo bash"
command -v systemctl >/dev/null 2>&1 || die "systemd not found; this installer targets systemd Linux."

# --------------------------------------------------------------------------
# Uninstall
# --------------------------------------------------------------------------
if [ "$MODE" = "uninstall" ]; then
  say "Stopping $SERVICE"
  systemctl disable --now "$SERVICE" 2>/dev/null || true
  rm -f "/etc/systemd/system/${SERVICE}.service"
  systemctl daemon-reload

  if [ "$PURGE" = "yes" ]; then
    rm -rf "$APP_DIR"
    userdel "$RUN_USER" 2>/dev/null || true
    say "Removed everything, including your settings and data."
  else
    if [ -f "$APP_DIR/data/app.db" ]; then
      backup="/root/movie-notifier-backup-$(date +%Y%m%d%H%M%S).db"
      cp "$APP_DIR/data/app.db" "$backup"
      chmod 600 "$backup"
      say "Service removed. Your data was copied to $backup"
    else
      say "Service removed."
    fi
    warn "App files are still in $APP_DIR — delete it, or re-run with --purge."
  fi
  exit 0
fi

# --------------------------------------------------------------------------
# Dependencies
# --------------------------------------------------------------------------
say "Checking dependencies"
NEED=()
command -v git >/dev/null 2>&1 || NEED+=(git)
command -v python3 >/dev/null 2>&1 || NEED+=(python3)
python3 -c "import venv" >/dev/null 2>&1 || NEED+=(python3-venv)

if [ ${#NEED[@]} -gt 0 ]; then
  if command -v apt-get >/dev/null 2>&1; then
    say "Installing: ${NEED[*]}"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${NEED[@]}"
  else
    die "Please install these first: ${NEED[*]}"
  fi
fi

PYV=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' \
  || die "Python 3.10+ required, found $PYV."
say "Python $PYV"

# --------------------------------------------------------------------------
# Fetch the code.
# Prefer a checkout we are already sitting in; otherwise clone.
# When piped from curl, BASH_SOURCE is unset, so this falls through to a clone.
# --------------------------------------------------------------------------
SRC=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  maybe=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
  # Plain `[ x ] && y` would abort the whole script under `set -e` when the
  # test fails, so this needs to be a real if.
  if [ -f "$maybe/run.py" ] && [ -d "$maybe/app" ]; then
    SRC="$maybe"
  fi
fi

if [ -n "$SRC" ] && [ "$SRC" != "$APP_DIR" ]; then
  say "Installing from local checkout: $SRC"
  mkdir -p "$APP_DIR"
  # .git is copied on purpose — self-update needs a real checkout to pull into.
  tar -C "$SRC" --exclude=data --exclude=.venv -cf - . | tar -C "$APP_DIR" -xf -
elif [ -d "$APP_DIR/.git" ]; then
  say "Updating existing install in $APP_DIR"
  git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
  git -C "$APP_DIR" reset --hard --quiet "origin/$BRANCH"
elif [ -z "$SRC" ]; then
  say "Cloning $REPO_URL"
  rm -rf "$APP_DIR.tmp"
  git clone --quiet --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR.tmp" \
    || die "Clone failed. If the repo is private, see the README for the token method."
  mkdir -p "$APP_DIR"
  # Keep any existing data/ — this may be a reinstall over a working setup.
  tar -C "$APP_DIR.tmp" -cf - . | tar -C "$APP_DIR" -xf -
  rm -rf "$APP_DIR.tmp"
fi

# --------------------------------------------------------------------------
# Virtualenv
# --------------------------------------------------------------------------
say "Installing Python packages (this is the slow part on a Pi)"
[ -d "$APP_DIR/.venv" ] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip setuptools wheel

if ! "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"; then
  # Usually Pillow with no prebuilt wheel for this architecture.
  warn "Install failed — adding image build dependencies and retrying."
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
      python3-dev libjpeg-dev zlib1g-dev libfreetype6-dev
  fi
  "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" \
    || die "Could not install Python packages."
fi

# --------------------------------------------------------------------------
# Service account + permissions.
# data/ holds the TMDB key and webhook URLs, so keep it owner-only.
# --------------------------------------------------------------------------
id "$RUN_USER" >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin "$RUN_USER"
mkdir -p "$APP_DIR/data"
chown -R "$RUN_USER:$RUN_USER" "$APP_DIR"
chmod 700 "$APP_DIR/data"
# Must be an if: on a fresh install there is no database yet, and a bare
# `[ -f ... ] && chmod` would exit the script here under `set -e`.
if [ -f "$APP_DIR/data/app.db" ]; then
  chmod 600 "$APP_DIR/data/app.db"
fi
chmod +x "$APP_DIR/install.sh" 2>/dev/null || true

# --------------------------------------------------------------------------
# systemd unit
# --------------------------------------------------------------------------
say "Writing /etc/systemd/system/${SERVICE}.service"
cat > "/etc/systemd/system/${SERVICE}.service" <<UNIT
[Unit]
Description=Movie Discord Notifier
Documentation=https://github.com/ZipperedJon/movie-discord-notifier
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/python run.py --host ${HOST} --port ${PORT}
# always, not on-failure: self-update finishes by exiting 0 so systemd brings
# the app back up on the new code.
Restart=always
RestartSec=5

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true
# The whole directory, not just data/: updating runs git and pip in here.
ReadWritePaths=${APP_DIR}

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --quiet "$SERVICE"
systemctl restart "$SERVICE"

sleep 3
if ! systemctl is-active --quiet "$SERVICE"; then
  echo
  die "Service failed to start. Logs:
  sudo journalctl -u $SERVICE -n 40 --no-pager"
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -n "$IP" ] || IP="<this-machine>"

cat <<DONE

  ${GREEN}${BOLD}Movie Discord Notifier is running.${RESET}

    Open:     ${BOLD}http://${IP}:${PORT}${RESET}
    Settings: ${BOLD}http://${IP}:${PORT}/settings${RESET}  ${DIM}(add your TMDB key + webhooks here)${RESET}

  ${DIM}Status:${RESET}   sudo systemctl status ${SERVICE}
  ${DIM}Logs:${RESET}     sudo journalctl -u ${SERVICE} -f
  ${DIM}Restart:${RESET}  sudo systemctl restart ${SERVICE}
  ${DIM}Update:${RESET}   sudo ${APP_DIR}/install.sh --update
  ${DIM}Remove:${RESET}   sudo ${APP_DIR}/install.sh --uninstall

DONE

if [ "$HOST" = "0.0.0.0" ]; then
  warn "The app has no login. Anyone on your network can open it and see your
    webhooks. That is fine on a home LAN — do not port-forward it to the
    internet. To bind it to this machine only, re-run with --host=127.0.0.1"
  echo
fi
