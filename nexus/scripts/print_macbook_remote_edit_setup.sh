#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

REMOTE_USER="${NEXUS_REMOTE_SSH_USER:-$(id -un)}"
REMOTE_HOST="${NEXUS_REMOTE_SSH_HOST:-$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || hostname)}"
REMOTE_PATH="${NEXUS_REMOTE_REPO_PATH:-$REPO_ROOT}"
SSH_ALIAS="${NEXUS_REMOTE_SSH_ALIAS:-nexus-mini}"
KEY_BASENAME="${NEXUS_REMOTE_SSH_KEY_NAME:-${SSH_ALIAS}-ed25519}"

python3 - <<'PY' "$REMOTE_USER" "$REMOTE_HOST" "$REMOTE_PATH" "$SSH_ALIAS" "$KEY_BASENAME"
import shlex
import sys

remote_user, remote_host, remote_path, ssh_alias, key_basename = sys.argv[1:6]
config_block = f"""Host {ssh_alias}
  HostName {remote_host}
  User {remote_user}
  IdentityFile ~/.ssh/{key_basename}
  IdentitiesOnly yes
  ServerAliveInterval 30
  ServerAliveCountMax 6
  StrictHostKeyChecking accept-new
"""
remote_uri = f'vscode-remote://ssh-remote+{ssh_alias}{remote_path}'

print("mkdir -p ~/.ssh")
print("chmod 700 ~/.ssh")
print(f"KEY_PATH=$HOME/.ssh/{shlex.quote(key_basename)}")
print('if [[ ! -f "$KEY_PATH" ]]; then')
print('  ssh-keygen -t ed25519 -f "$KEY_PATH" -N "" -C "nexus-remote-edit-$(hostname)"')
print("fi")
print("touch ~/.ssh/config")
print("chmod 600 ~/.ssh/config")
print("python3 - <<'PY2'")
print("from pathlib import Path")
print("")
print("config_path = Path.home() / '.ssh' / 'config'")
print(f"block = {config_block!r}")
print("existing = config_path.read_text() if config_path.exists() else ''")
print(f"if 'Host {ssh_alias}\\n' not in existing and 'Host {ssh_alias}\\r\\n' not in existing:")
print("    if existing and not existing.endswith('\\n'):")
print("        existing += '\\n'")
print("    config_path.write_text(existing + block)")
print("PY2")
print(f"cat \"$KEY_PATH.pub\" | ssh {shlex.quote(ssh_alias)} 'umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; chmod 600 ~/.ssh/authorized_keys; key=$(cat); grep -qxF \"$key\" ~/.ssh/authorized_keys || printf \"%s\\n\" \"$key\" >> ~/.ssh/authorized_keys'")
print(f"ssh {shlex.quote(ssh_alias)} {shlex.quote(f'cd {remote_path} && pwd')}")
print("echo")
print("echo 'Open the Mac mini repo directly from the MacBook with:'")
print(f"echo '  code --folder-uri {shlex.quote(remote_uri)}'")
print("echo 'Then, inside that remote repo window, run:'")
print("echo '  ./scripts/nexus_claude_bootstrap.sh'")
PY
