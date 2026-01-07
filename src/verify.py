import argparse
import logging
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from typing import Any, Dict

from utils import (
    CONFIG_FILE,
    get_repositories,
    load_config,
    run_command,
    setup_repo_env,
)

# Re-configure logging to separate file
# We need to remove handlers that backup.py might have set up (if it were imported, but now it's not)
# But just in case
root_logger = logging.getLogger()
for h in root_logger.handlers[:]:
    root_logger.removeHandler(h)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler(
            'log_verify.log', maxBytes=50 * 1024 * 1024, backupCount=10
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def verify_server(server_conf: Dict[str, Any], args: argparse.Namespace) -> None:
    """Run verification for a given server."""
    repositories = get_repositories(server_conf)

    for repo in repositories:
        repo_name = repo.get('name', 'default')
        repo_path = repo['path']
        logger.info(f'Verifying {server_conf["name"]} (Repo: {repo_name})...')

        env = setup_repo_env(repo, server_conf)

        if args.unlock:
            cmd = ['restic', '-r', repo_path, 'unlock']
            logger.info(
                f"Unlocking server '{server_conf['name']}' repo: '{repo_name}'..."
            )
        else:
            cmd = ['restic', '-r', repo_path, 'check']
            if args.full:
                cmd.append('--read-data')

        try:
            run_command(cmd, env=env)
            if not args.unlock:
                logger.info(
                    f"✅ Verification passed for '{server_conf['name']}' repo: '{repo_name}'."
                )
        except subprocess.CalledProcessError as e:
            if e.returncode == 10:
                logger.warning(f"⁉️ Repository for '{server_conf['name']}' - '{repo_name}' does not exist yet (skipping).")
            elif args.unlock:
                logger.error(
                    f"❌ Unlock FAILED for '{server_conf['name']}' repo: '{repo_name}': {e}"
                )
            else:
                logger.error(
                    f"❌ Verification FAILED for '{server_conf['name']}' repo: '{repo_name}': {e}"
                )


def main() -> None:
    parser = argparse.ArgumentParser(description='Verify or Unlock Restic backups')
    parser.add_argument(
        '--full', action='store_true', help='Perform full data read (slow)'
    )
    parser.add_argument('--unlock', action='store_true', help='Remove stale locks')
    parser.add_argument(
        'server', nargs='?', help='Specific server to verify (optional)'
    )

    args = parser.parse_args()

    config = load_config(CONFIG_FILE)

    servers = []
    if args.server:
        servers = [s for s in config['servers'] if s['name'] == args.server]
        if not servers:
            logger.error(f"Server '{args.server}' not found.")
            sys.exit(1)
    else:
        servers = config['servers']

    for server in servers:
        verify_server(server, args)


if __name__ == '__main__':
    main()
