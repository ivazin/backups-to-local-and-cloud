import argparse
import logging
import os
import sys
from typing import Any, Dict, Optional

from utils import (
    CONFIG_FILE,
    get_repositories,
    load_config,
    run_command,
    setup_repo_env,
)

# Reuse configuration logic from backup.py
logging.basicConfig(
    level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def list_snapshots(
    server_conf: Dict[str, Any], repo_filter: Optional[str] = None
) -> None:
    """List snapshots for a given server."""
    repositories = get_repositories(server_conf)

    for repo in repositories:
        name = repo.get('name', 'default')
        if repo_filter and repo_filter != name:
            continue

        repo_path = repo['path']
        logger.info(f'Listing snapshots for {server_conf["name"]} (Repo: {name})...')

        env = setup_repo_env(repo, server_conf)

        try:
            cmd = ['restic', '-r', repo_path, 'snapshots']
            run_command(cmd, env=env)
        except Exception as e:
            logger.error(f'Failed to list snapshots for repo {name}: {e}')


def restore_snapshot(
    server_conf: Dict[str, Any],
    snapshot_id: str,
    target_path: str,
    repo_filter: Optional[str] = None,
) -> None:
    """Restore a specific snapshot."""
    repositories = get_repositories(server_conf)

    # If no filter, use first repo? Or fail?
    # Default to first if none specified
    target_repo = None
    if repo_filter:
        target_repo = next(
            (r for r in repositories if r.get('name', 'default') == repo_filter), None
        )
        if not target_repo:
            logger.error(f'Repository {repo_filter} not found.')
            sys.exit(1)
    else:
        target_repo = repositories[0]
        logger.info(
            f'No repository specified, using default: {target_repo.get("name", "default")}'
        )

    repo_path = target_repo['path']
    repo_name = target_repo.get('name', 'default')

    logger.info(
        f'Restoring snapshot {snapshot_id} for {server_conf["name"]} (Repo: {repo_name}) to {target_path}...'
    )

    if not os.path.exists(target_path):
        os.makedirs(target_path)

    env = setup_repo_env(target_repo, server_conf)

    cmd = ['restic', '-r', repo_path, 'restore', snapshot_id, '--target', target_path]
    run_command(cmd, env=env)


def main() -> None:
    parser = argparse.ArgumentParser(description='Restore helper for Restic backups')
    parser.add_argument(
        'server', help='Name of the server to restore from (as defined in servers.yaml)'
    )
    parser.add_argument('--list', action='store_true', help='List available snapshots')
    parser.add_argument(
        '--restore', metavar='SNAPSHOT_ID', help="Snapshot ID to restore (or 'latest')"
    )
    parser.add_argument(
        '--target',
        default='./restore_out',
        help='Directory to restore to (default: ./restore_out)',
    )
    parser.add_argument(
        '--repo', help='Repository name to use (default: first available)'
    )

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
                    print(f'\n{"=" * 20} {s["name"]} {"=" * 20}')
                list_snapshots(s, repo_filter=args.repo)
        elif args.restore:
            # Restore only works for single server (handled by logic above for 'all')
            # Check for --restore value, if it's None, we should probably error or handle it
            if args.restore is None:
                logger.error('Snapshot ID is required for restore.')
                sys.exit(1)

            restore_snapshot(
                servers[0], args.restore, args.target, repo_filter=args.repo
            )
        else:
            parser.print_help()

    except Exception as e:
        logger.error(f'Operation failed: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
