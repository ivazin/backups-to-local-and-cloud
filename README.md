# Restic Backup Automation (Remote to Local)

This script automates backing up remote directories to a local Restic repository using SSHFS.
It allows you to define multiple servers and specific include/exclude patterns for each.

## Prerequisites

1.  **Python 3**
2.  **Restic**: `brew install restic`
## Prerequisites

1.  **Python 3**
2.  **Restic**: `brew install restic`
3.  **Rsync**: `rsync` is usually pre-installed on macOS, but a newer version is recommended:
    ```bash
    brew install rsync
    ```
4.  **Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```
5.  **Rclone**: `brew install rclone`, `rclone config`, new "webdav" config for yandex. 

## Configuration

Edit `servers.yaml` to define your servers:

```yaml
servers:
  - name: "my-vps"
    host: "root@my-vps.com"
    repo_path: "./repos/my-vps"
    # Local directory to mirror remote files to (requires disk space)
    mirror_path: "./tmp/mirror_vps"
    
    # Define paths with specific excludes
    paths:
      - path: "/etc"
      - path: "/var/www"
        exclude: ["cache", "node_modules"] # Excludes /var/www/cache, etc.

    # Global excludes (applies to all)
    exclude:
      - "*.log"
      
    # Command to get password from 1Password
    password_command: "op item get restic-repo-password --fields password_server_1"
```

## Usage

1.  Set your Restic password (so it doesn't ask interactively):
    ```bash
    export RESTIC_PASSWORD="your-secure-password"
    ```
    
2.  Run the script:
    ```bash
    <!-- python3 backup.py -->
    uv run --with PyYAML backup.py
    ```

## How it works

1.  Reads `servers.yaml`.
2.  Initializes a Restic repository at `repo_path` if it doesn't exist.
3.  Mounts the remote server filesystem (via SSH) to `mount_point` using `sshfs`.
4.  Runs `restic backup` on the mapped local paths.
5.  Unmounts and cleans up.


## How to restore
1.  Get Restic stapshot ID:
    ```bash
    uv run --with PyYAML restore.py --list pi
    uv run --with PyYAML restore.py --list pi --repo yandex
    ```
2.  Restore it to target directory:
    ```bash
    uv run --with PyYAML restore.py --snapshot <snapshot_id> --target ./tmp/restore_pi
    uv run --with PyYAML restore.py --restore latest --target ./out --repo yandex pi
    ```

## Makefile Shortcuts

A `Makefile` is provided for convenience:

*   **Backup all servers**:
    ```bash
    make backup
    ```
*   **List all snapshots**:
    ```bash
    make show-backups
    ```

