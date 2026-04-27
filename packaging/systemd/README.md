# Agent Baton systemd Service

Run the Agent Baton daemon as a managed systemd service.

## Prerequisites

1. **System user** — create a dedicated `baton` user and group:

   ```
   sudo useradd --system --no-create-home --shell /usr/sbin/nologin baton
   ```

2. **`baton` CLI on PATH** — the unit assumes `/usr/local/bin/baton`.
   Verify with `which baton`.

3. **systemd** — required (any modern Linux distro).

## Install

```
sudo bash install.sh
```

Pass `--force` to overwrite an existing installation:

```
sudo bash install.sh --force
```

## Activate

```
sudo systemctl enable --now agent-baton-daemon
```

## Verify status

```
sudo systemctl status agent-baton-daemon
```

## View logs

```
journalctl -u agent-baton-daemon -f
```

## Uninstall

```
sudo systemctl disable --now agent-baton-daemon
sudo rm /etc/systemd/system/agent-baton-daemon.service
sudo systemctl daemon-reload
```
