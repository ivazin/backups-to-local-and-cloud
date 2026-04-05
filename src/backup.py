import logging
import os
import shutil
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from typing import Any, Dict

from utils import (
    CONFIG_FILE,
    get_password_value,
    get_repositories,
    load_config,
    run_command,
    setup_repo_env,
)

# Configuration


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler(
            'log_backup.log', maxBytes=50 * 1024 * 1024, backupCount=10
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def ensure_repo_init(server_conf: Dict[str, Any]) -> None:
    """Initialize restic repo if config file doesn't exist."""
    repo_path = server_conf['repo_path']
    if not os.path.exists(os.path.join(repo_path, 'config')):
        logger.info(f'Initializing restic repository at {repo_path}')
        env = os.environ.copy()
        password = get_password_value(server_conf.get('password_command'))
        if password:
            env['RESTIC_PASSWORD'] = password
        run_command(['restic', '-r', repo_path, 'init'], env=env)
    else:
        logger.info(f'Repository allows exists at {repo_path}')


def sync_paths(server_conf: Dict[str, Any]) -> bool:
    """
    Rsync remote paths to local mirror directory.
    Replaces SSHFS mount strategy.
    """
    host = server_conf.get('host')

    # If host is localhost or missing, it's a local backup. Skip sync.
    if not host or host == 'localhost':
        logger.info(f'Server {server_conf["name"]} is local. Skipping rsync.')
        return True

    port = str(server_conf.get('port', 22))
    mirror_path = server_conf.get('mirror_path')  # renamed from mount_point

    # Fallback for backward compatibility if user hasn't updated config yet
    if not mirror_path and 'mount_point' in server_conf:
        mirror_path = server_conf['mount_point']

    if not mirror_path:
        raise ValueError('No mirror_path (or mount_point) defined.')

    # Includes parsing
    includes = server_conf.get('include', [])
    paths_conf = server_conf.get('paths', [])

    # Normalize targets
    targets = []
    for inc in includes:
        targets.append({'path': inc.lstrip('/'), 'excludes': []})
    for p_conf in paths_conf:
        targets.append(
            {'path': p_conf['path'].lstrip('/'), 'excludes': p_conf.get('exclude', [])}
        )

    if not targets:
        logger.warning(f'No paths to sync for {server_conf["name"]}')
        return True

    if not os.path.exists(mirror_path):
        os.makedirs(mirror_path)

    logger.info(f'Syncing paths from {host} to {mirror_path}...')

    ssh_cmd = f'ssh -p {port}'
    global_excludes = server_conf.get('exclude', [])

    for t_obj in targets:
        target = t_obj['path']
        t_excludes = t_obj['excludes']

        # Split target to find parent dir
        parent_dir = os.path.dirname(target)
        local_parent = os.path.join(mirror_path, parent_dir)

        if not os.path.exists(local_parent):
            os.makedirs(local_parent)

        source_path = f'/{target}'  # Absolute remote path
        destination = local_parent + '/'

        # Rsync command
        # Add --log-file to a specific rsync log to capture details
        rsync_log = 'log_rsync_last_run.log'

        cmd = [
            'rsync',
            '-az',
            '--delete',
            '--info=progress2',
            f'--log-file={rsync_log}',
            '-e',
            ssh_cmd,
        ]

        # Add Excludes
        # Rsync excludes are relative to the transfer root.
        # If we transfer /home/user/Documents/dockers, then pattern "mywebcamsweb/nginx_cache" matches inside that.
        # Global excludes (like *.log) should typically apply everywhere.
        for ex in global_excludes:
            cmd.extend(['--exclude', ex])

        if server_conf.get('use_sudo', False):
            cmd.extend(['--rsync-path', 'sudo rsync'])

        for ex in t_excludes:
            # If exclude is absolute, rsync anchors it to root of transfer.
            # But users might provide relative paths in config meant for restic.
            # "mywebcamsweb/nginx_cache" -> OK for rsync if inside transfer root.
            cmd.extend(['--exclude', ex])

        cmd.extend([f'{host}:{source_path}', destination])

        try:
            # Capture output for rsync so we can see stderr in log on failure
            run_command(cmd, check=True, capture_output=True)
        except Exception as e:
            logger.error(f'Failed to sync {source_path}: {e}')
            # Decide: stop or continue? Continue best effort.
            # If rsync fails, we probably shouldn't backup this server to avoid "missing files" errors
            # or backing up empty/stale state if we were to proceed.
            # BUT: current logic was "continue". The user issue shows that "missing files" is a fatal error later.
            # Let's return False if ANY sync fails, so we can skip backup.
            return False

    return True


def perform_backup(server_conf: Dict[str, Any]) -> Dict[str, str]:
    is_local = not server_conf.get('host') or server_conf.get('host') == 'localhost'

    mirror_path = None
    if not is_local:
        mirror_path = server_conf.get('mirror_path') or server_conf.get('mount_point')

    # Includes parsing
    includes = server_conf.get('include', [])
    paths_conf = server_conf.get('paths', [])
    global_excludes = server_conf.get('exclude', [])

    # Normalize targets
    targets = []
    for inc in includes:
        targets.append({'path': inc.lstrip('/'), 'excludes': []})
    for p_conf in paths_conf:
        targets.append(
            {'path': p_conf['path'].lstrip('/'), 'excludes': p_conf.get('exclude', [])}
        )

    if not targets:
        logger.warning(f'No paths configured to backup for {server_conf["name"]}')
        return {}

    # Iterate Repositories
    repositories = get_repositories(server_conf)
    results = {}

    for repo in repositories:
        repo_path = repo['path']
        repo_name = repo.get('name', 'default')
        logger.info(
            f'Starting restic backup for {server_conf["name"]} (Repo: {repo_name}) from {mirror_path}...'
        )

        env = setup_repo_env(repo, server_conf)

        # Init Check
        try:
            # Check config existence siliently (don't log error if fails, as it just means we need to init)
            run_command(
                ['restic', '-r', repo_path, 'cat', 'config'],
                env=env,
                check=True,
                capture_output=True,
                log_error=False,
            )
            logger.info(f"Repository '{repo_name}' exists.")
        except Exception:
            logger.info(
                f"Initializing repository '{repo_name}' for '{server_conf['name']}' at {repo_path}"
            )
            try:
                run_command(['restic', '-r', repo_path, 'init'], env=env)
            except Exception as e:
                logger.error(
                    f"❌ Failed to init repo '{repo_name}' for '{server_conf['name']}': {e}"
                )
                results[repo_name] = f"INIT_FAILED: {e}"
                continue

        cmd = ['restic', '-r', repo_path, 'backup']

        # Add global excludes
        for exclude_pattern in global_excludes:
            cmd.extend(['--exclude', exclude_pattern])

        # Add path-specific excludes
        for t in targets:
            # base_path unused removed
            for exclude_pattern in t['excludes']:
                # If user wrote "mywebcamsweb/nginx_cache", we might want to prefix with base path?
                # Or just pass it as is? "mywebcamsweb/nginx_cache" works as pattern usually.
                # But absolute paths in exclude list need care.
                if exclude_pattern.startswith('/'):
                    # Likely an absolute remote path constraint which we handle in rsync.
                    # In restic, if we pass absolute path, it won't match relative storage.
                    # We skip absolute excludes here or assume they are patterns?
                    # Let's strip leading slash to be safe pattern
                    cmd.extend(['--exclude', exclude_pattern.lstrip('/')])
                else:
                    cmd.extend(['--exclude', exclude_pattern])

        # Add tags
        cmd.extend(['--tag', server_conf['name']])

        # Add sources
        sources = [t['path'] for t in targets]
        cmd.extend(sources)

        try:
            logger.info(
                f"Running backup command for '{server_conf['name']}' (Repo: '{repo_name}')..."
            )

            # Determine CWD
            # If Remote/Mirror: run from mirror_path
            # If Local: run from root (/) or user's CWD
            cwd = None
            is_local = (
                not server_conf.get('host') or server_conf.get('host') == 'localhost'
            )

            if is_local:
                # limit cwd to root so absolute paths work
                cwd = '/'
            else:
                cwd = mirror_path

            subprocess.run(cmd, check=True, cwd=cwd, env=env)
            logger.info(
                f"✅ Backup for '{server_conf['name']}' (Repo: '{repo_name}') completed successfully."
            )
            results[repo_name] = "SUCCESS"
        except subprocess.CalledProcessError as e:
            logger.error(
                f"❌ Backup failed for '{server_conf['name']}' (Repo: '{repo_name}'): {e}"
            )
            results[repo_name] = f"FAILED: {e}"

    return results


def prune_repositories(server_conf: Dict[str, Any]) -> Dict[str, str]:
    """Run prune for all repositories of a server."""
    repositories = get_repositories(server_conf)
    results = {}

    retention = server_conf.get('retention', {})
    if not retention:
        return {}

    for repo in repositories:
        repo_path = repo['path']
        repo_name = repo.get('name', 'default')

        logger.info(
            f'Pruning snapshots for {server_conf["name"]} (Repo: {repo_name})...'
        )

        env = setup_repo_env(repo, server_conf)

        cmd = ['restic', '-r', repo_path, 'forget', '--prune']

        # Standard policies
        policy_found = False
        for key in [
            'keep_last',
            'keep_daily',
            'keep_hourly',
            'keep_weekly',
            'keep_monthly',
            'keep_yearly',
            'keep_within',
        ]:
            val = retention.get(key)
            if val is not None:
                flag = '--' + key.replace('_', '-')
                cmd.extend([flag, str(val)])
                policy_found = True

        if not policy_found:
            # Default
            cmd.extend(['--keep-last', '10'])

        try:
            run_command(cmd, env=env)
            results[repo_name] = "SUCCESS"
        except Exception as e:
            logger.error(f'❌ Prune failed for {repo_name}: {e}')
            results[repo_name] = f"FAILED: {e}"

    return results


def main() -> None:
    if 'RESTIC_PASSWORD' not in os.environ:
        logger.info(
            "RESTIC_PASSWORD not set globally. Expecting 'password_command' in server configs."
        )

    # Check for rsync
    if subprocess.call(['which', 'rsync'], stdout=subprocess.DEVNULL) != 0:
        logger.error('rsync is not installed. Please install it.')
        sys.exit(1)

    config = load_config(CONFIG_FILE)
    all_reports = []

    for server in config['servers']:
        report = {
            'name': server['name'],
            'sync': 'SKIPPED',
            'backups': {},
            'prunes': {},
            'cleanup': 'SKIPPED',
            'success': True,
        }
        try:
            logger.info(f'Processing server: {server["name"]}')

            # 1. Sync files to local mirror
            if not sync_paths(server):
                logger.error(f"❌ Sync failed for {server['name']}. Skipping backup.")
                report['sync'] = 'FAILED'
                report['success'] = False
                all_reports.append(report)
                continue

            host = server.get('host')
            if host and host != 'localhost':
                report['sync'] = 'SUCCESS'

            # 2. Backup from local mirror to ALL repositories
            backup_results = perform_backup(server)
            report['backups'] = backup_results
            if any('FAILED' in str(v) for v in backup_results.values()):
                report['success'] = False

            # 3. Prune ALL repositories
            if server.get('prune', True):
                prune_results = prune_repositories(server)
                report['prunes'] = prune_results
                if any('FAILED' in str(v) for v in prune_results.values()):
                    report['success'] = False

            # 4. Optional Cleanup
            if server.get('cleanup', False):
                mirror_path = server.get('mirror_path') or server.get('mount_point')
                if mirror_path and os.path.exists(mirror_path):
                    logger.info(f'Cleaning up mirror paths {mirror_path}...')
                    try:
                        shutil.rmtree(mirror_path)
                        report['cleanup'] = 'SUCCESS'
                    except OSError as e:
                        logger.warning(f'Cleanup warning (non-critical): {e}')
                        report['cleanup'] = f'WARNING: {e}'

        except Exception as e:
            logger.error(f'Failed to process {server["name"]}: {e}')
            report['success'] = False
            report['error'] = str(e)

        all_reports.append(report)

    # Final Report
    print("\n" + "=" * 80)
    print(f"{'BACKUP EXECUTION SUMMARY':^80}")
    print("=" * 80)
    print(f"{'Server':<25} | {'Sync':<10} | {'Backups':<20} | {'Prunes':<15}")
    print("-" * 80)

    for r in all_reports:
        name = r['name'][:25]
        sync = r['sync']

        # Format backups status
        b_ok = sum(1 for v in r['backups'].values() if v == "SUCCESS")
        b_total = len(r['backups'])
        backups_str = f"{b_ok}/{b_total} OK" if b_total > 0 else "N/A"

        # Format prunes status
        p_ok = sum(1 for v in r['prunes'].values() if v == "SUCCESS")
        p_total = len(r['prunes'])
        prunes_str = f"{p_ok}/{p_total} OK" if p_total > 0 else "N/A"

        print(f"{name:<25} | {sync:<10} | {backups_str:<20} | {prunes_str:<15}")

        # Individual repo failures if any
        for repo, status in r['backups'].items():
            if status != "SUCCESS":
                print(f"  └─ Repo '{repo}' Backup: {status}")
        for repo, status in r['prunes'].items():
            if status != "SUCCESS":
                print(f"  └─ Repo '{repo}' Prune: {status}")
        if 'error' in r:
            print(f"  └─ Critical Error: {r['error']}")

    print("=" * 80)

    # Exit with error code if any server failed
    if any(not r['success'] for r in all_reports):
        sys.exit(1)


if __name__ == '__main__':
    main()
