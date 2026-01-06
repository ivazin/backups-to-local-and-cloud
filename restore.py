import argparse
import logging
import os
import sys
import subprocess
from backup import load_config, get_password, CONFIG_FILE

# Reuse configuration logic from backup.py
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def run_command(cmd, env=None, check=True):
    """Run a shell command."""
    logger.info(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=check, env=env)

def list_snapshots(server_conf):
    """List snapshots for a given server."""
    repo_path = server_conf['repo_path']
    logger.info(f"Listing snapshots for {server_conf['name']}...")
    
    env = os.environ.copy()
    env['RESTIC_PASSWORD'] = get_password(server_conf)
    
    cmd = ["restic", "-r", repo_path, "snapshots"]
    run_command(cmd, env=env)

def restore_snapshot(server_conf, snapshot_id, target_path):
    """Restore a specific snapshot."""
    repo_path = server_conf['repo_path']
    logger.info(f"Restoring snapshot {snapshot_id} for {server_conf['name']} to {target_path}...")
    
    if not os.path.exists(target_path):
        os.makedirs(target_path)
        
    env = os.environ.copy()
    env['RESTIC_PASSWORD'] = get_password(server_conf)
    
    cmd = ["restic", "-r", repo_path, "restore", snapshot_id, "--target", target_path]
    run_command(cmd, env=env)

def main():
    parser = argparse.ArgumentParser(description="Restore helper for Restic backups")
    parser.add_argument("server", help="Name of the server to restore from (as defined in servers.yaml)")
    parser.add_argument("--list", action="store_true", help="List available snapshots")
    parser.add_argument("--restore", metavar="SNAPSHOT_ID", help="Snapshot ID to restore (or 'latest')")
    parser.add_argument("--target", default="./restore_out", help="Directory to restore to (default: ./restore_out)")
    
    args = parser.parse_args()

    config = load_config(CONFIG_FILE)
    
    # Find server config
    if args.server == 'all':
        if not args.list:
             logger.error("The 'all' keyword is only supported with --list")
             sys.exit(1)
        servers = config['servers']
    else:
        s = next((s for s in config['servers'] if s['name'] == args.server), None)
        if not s:
            logger.error(f"Server '{args.server}' not found in {CONFIG_FILE}")
            sys.exit(1)
        servers = [s]

    try:
        if args.list:
            for s in servers:
                # Add a header if listing multiple
                if len(servers) > 1:
                    print(f"\n{'='*20} {s['name']} {'='*20}")
                list_snapshots(s)
        elif args.restore:
            # Restore only works for single server (handled by logic above for 'all')
            restore_snapshot(servers[0], args.restore, args.target)
        else:
            parser.print_help()
            
    except Exception as e:
        logger.error(f"Operation failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
