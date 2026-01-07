import logging
import os
import subprocess
from typing import Any, Dict, List, Optional

import yaml  # type: ignore

# Configuration
CONFIG_FILE = 'servers.yaml'

logger = logging.getLogger(__name__)


def load_config(path: str) -> Any:
    with open(path, 'r') as f:
        return yaml.safe_load(f)


def run_command(
    cmd: List[str],
    env: Optional[Dict[str, str]] = None,
    check: bool = True,
    capture_output: bool = False,
    log_error: bool = True,
) -> None:
    """Run a shell command."""
    # Don't log full environment as it may contain passwords
    logger.info(f'Running: {" ".join(cmd)}')
    try:
        if capture_output:
            subprocess.run(cmd, check=check, env=env, capture_output=True, text=True)
        else:
            subprocess.run(cmd, check=check, env=env)
    except subprocess.CalledProcessError as e:
        if log_error:
            logger.error(f'Command failed: {e}')
            if capture_output and e.stderr:
                logger.error(f'Command stderr:\n{e.stderr}')
        if check:
            raise


def get_password_value(cmd_str: Optional[str]) -> Optional[str]:
    """Run command to get password."""
    if not cmd_str:
        return None
    try:
        logger.info(f'Retrieving password using command: {cmd_str}')
        return subprocess.check_output(cmd_str, shell=True).strip().decode('utf-8')
    except Exception as e:
        logger.error(f'Failed to get password: {e}')
        return None


def get_repositories(server_conf: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Get repositories list from server config, handling backward compatibility."""
    repositories = server_conf.get('repositories', [])
    if not repositories and 'repo_path' in server_conf:
        repositories.append(
            {
                'name': 'default',
                'path': server_conf['repo_path'],
                'password_command': server_conf.get('password_command'),
            }
        )
    return repositories


def setup_repo_env(repo: Dict[str, Any], server_conf: Dict[str, Any]) -> Dict[str, str]:
    """Setup environment variables for a repository."""
    password_value = get_password_value(repo.get('password_command'))
    if not password_value:
        password_value = get_password_value(server_conf.get('password_command'))

    env = os.environ.copy()
    if password_value:
        env['RESTIC_PASSWORD'] = password_value

    repo_env = repo.get('env', {})
    for k, v in repo_env.items():
        if v.startswith('op '):
            try:
                val = subprocess.check_output(v, shell=True).decode().strip()
                env[k] = val
            except Exception as ex:
                logger.warning(f'Failed to resolve env var {k}: {ex}')
        else:
            env[k] = str(v)
    return env
