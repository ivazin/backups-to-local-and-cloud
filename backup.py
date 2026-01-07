import yaml
import subprocess
import os
import sys
import logging
import time
import shutil
from logging.handlers import RotatingFileHandler

# Configuration
CONFIG_FILE = "servers.yaml"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler("log_backup.log", maxBytes=50*1024*1024, backupCount=10),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

def load_config(path):
    with open(path, 'r') as f:
        return yaml.safe_load(f)

def run_command(cmd, env=None, check=True, capture_output=False, log_error=True):
    """Run a shell command."""
    # Don't log full environment as it may contain passwords
    logger.info(f"Running: {' '.join(cmd)}")
    try:
        if capture_output:
             subprocess.run(cmd, check=check, env=env, capture_output=True, text=True)
        else:
             subprocess.run(cmd, check=check, env=env)
    except subprocess.CalledProcessError as e:
        if log_error:
            logger.error(f"Command failed: {e}")
            if capture_output and e.stderr:
                logger.error(f"Command stderr:\n{e.stderr}")
        if check:
            raise

def get_password_value(cmd_str):
    """Run command to get password."""
    if not cmd_str:
        return None
    try:
        logger.info(f"Retrieving password using command: {cmd_str}")
        return subprocess.check_output(cmd_str, shell=True).strip().decode('utf-8')
    except Exception as e:
        logger.error(f"Failed to get password: {e}")
        return None

def ensure_repo_init(server_conf):
    """Initialize restic repo if config file doesn't exist."""
    repo_path = server_conf['repo_path']
    if not os.path.exists(os.path.join(repo_path, "config")):
        logger.info(f"Initializing restic repository at {repo_path}")
        env = os.environ.copy()
        env['RESTIC_PASSWORD'] = get_password(server_conf)
        run_command(["restic", "-r", repo_path, "init"], env=env)
    else:
        logger.info(f"Repository allows exists at {repo_path}")

def sync_paths(server_conf):
    """
    Rsync remote paths to local mirror directory.
    Replaces SSHFS mount strategy.
    """
    host = server_conf['host']
    port = str(server_conf.get('port', 22))
    mirror_path = server_conf.get('mirror_path') # renamed from mount_point
    
    # Fallback for backward compatibility if user hasn't updated config yet
    if not mirror_path and 'mount_point' in server_conf:
        mirror_path = server_conf['mount_point']
        
    if not mirror_path:
        raise ValueError("No mirror_path (or mount_point) defined.")

    # Includes parsing
    includes = server_conf.get('include', [])
    paths_conf = server_conf.get('paths', [])
    
    # Normalize targets
    targets = []
    for inc in includes:
        targets.append({
            'path': inc.lstrip('/'),
            'excludes': []
        })
    for p_conf in paths_conf:
        targets.append({
            'path': p_conf['path'].lstrip('/'),
            'excludes': p_conf.get('exclude', [])
        })

    if not targets:
        logger.warning(f"No paths to sync for {server_conf['name']}")
        return

    if not os.path.exists(mirror_path):
        os.makedirs(mirror_path)

    logger.info(f"Syncing paths from {host} to {mirror_path}...")

    ssh_cmd = f"ssh -p {port}"
    global_excludes = server_conf.get('exclude', [])
    
    for t_obj in targets:
        target = t_obj['path']
        t_excludes = t_obj['excludes']
        
        # Split target to find parent dir
        parent_dir = os.path.dirname(target)
        local_parent = os.path.join(mirror_path, parent_dir)
        
        if not os.path.exists(local_parent):
            os.makedirs(local_parent)
            
        source_path = f"/{target}" # Absolute remote path
        destination = local_parent + "/" 
        
        # Rsync command
        # Add --log-file to a specific rsync log to capture details
        rsync_log = "log_rsync_last_run.log"
        
        cmd = [
            "rsync", "-az", "--delete", "--info=progress2",
            f"--log-file={rsync_log}",
            "-e", ssh_cmd
        ]
        
        # Add Excludes
        # Rsync excludes are relative to the transfer root.
        # If we transfer /home/user/Documents/dockers, then pattern "mywebcamsweb/nginx_cache" matches inside that.
        # Global excludes (like *.log) should typically apply everywhere.
        for ex in global_excludes:
            cmd.extend(["--exclude", ex])
            
        for ex in t_excludes:
            # If exclude is absolute, rsync anchors it to root of transfer.
            # But users might provide relative paths in config meant for restic.
            # "mywebcamsweb/nginx_cache" -> OK for rsync if inside transfer root.
            cmd.extend(["--exclude", ex])
            
        cmd.extend([f"{host}:{source_path}", destination])
        
        try:
            # Capture output for rsync so we can see stderr in log on failure
            run_command(cmd, check=True, capture_output=True)
        except Exception as e:
            logger.error(f"Failed to sync {source_path}: {e}")
            # Decide: stop or continue? Continue best effort.
            continue

def perform_backup(server_conf):
    mirror_path = server_conf.get('mirror_path') or server_conf.get('mount_point')
    
    # Includes parsing
    includes = server_conf.get('include', [])
    paths_conf = server_conf.get('paths', [])
    global_excludes = server_conf.get('exclude', [])
    
    # Normalize targets
    targets = []
    for inc in includes:
        targets.append({
            'path': inc.lstrip('/'),
            'excludes': []
        })
    for p_conf in paths_conf:
        targets.append({
            'path': p_conf['path'].lstrip('/'),
            'excludes': p_conf.get('exclude', [])
        })

    if not targets:
        logger.warning(f"No paths configured to backup for {server_conf['name']}")
        return

    # Iterate Repositories
    repositories = server_conf.get('repositories', [])
    if not repositories and 'repo_path' in server_conf:
        repositories.append({
            'name': 'default',
            'path': server_conf['repo_path'],
            'password_command': server_conf.get('password_command')
        })

    for repo in repositories:
        repo_path = repo['path']
        repo_name = repo.get('name', 'default')
        logger.info(f"Starting restic backup for {server_conf['name']} (Repo: {repo_name}) from {mirror_path}...")

        # Get Password
        password_value = get_password_value(repo.get('password_command'))
        if not password_value:
             password_value = get_password_value(server_conf.get('password_command'))
        
        env = os.environ.copy()
        if password_value:
            env['RESTIC_PASSWORD'] = password_value
            
        # Add repo-specific env
        repo_env = repo.get('env', {})
        for k, v in repo_env.items():
             if v.startswith("op "):
                  try:
                      val = subprocess.check_output(v, shell=True).decode().strip()
                      env[k] = val
                  except Exception as ex:
                      logger.warning(f"Failed to resolve env var {k} for repo {repo_name}: {ex}")
             else:
                  env[k] = v

        # Init Check
        try:
             # Check config existence siliently (don't log error if fails, as it just means we need to init)
             run_command(["restic", "-r", repo_path, "cat", "config"], env=env, check=True, capture_output=True, log_error=False)
             logger.info(f"Repository {repo_name} exists.")
        except:
             logger.info(f"Initializing repository {repo_name} at {repo_path}")
             try:
                 run_command(["restic", "-r", repo_path, "init"], env=env)
             except Exception as e:
                 logger.error(f"Failed to init repo {repo_name}: {e}")
                 continue

        cmd = ["restic", "-r", repo_path, "backup"]
        
        # Add global excludes
        for ex in global_excludes:
            cmd.extend(["--exclude", ex])
            
        # Add path-specific excludes
        # Note: We must be careful about relative paths.
        # Restic sees paths relative to mirror_path root.
        # e.g. mirror_path/home/user/Documents -> stored as "home/user/Documents"
        # exclude "mywebcamsweb/nginx_cache" should validly match "home/user/Documents/mywebcamsweb/nginx_cache" 
        # IF we exclude based on patterns.
        # Restic exclude patterns are flexible.
        for t in targets:
            base_path = t['path'] # e.g. home/user/Documents
            for ex in t['excludes']:
                 # If user wrote "mywebcamsweb/nginx_cache", we might want to prefix with base path?
                 # Or just pass it as is? "mywebcamsweb/nginx_cache" works as pattern usually.
                 # But absolute paths in exclude list need care.
                 if ex.startswith('/'):
                     # Likely an absolute remote path constraint which we handle in rsync.
                     # In restic, if we pass absolute path, it won't match relative storage.
                     # We skip absolute excludes here or assume they are patterns?
                     # Let's strip leading slash to be safe pattern
                     cmd.extend(["--exclude", ex.lstrip('/')])
                 else:
                     cmd.extend(["--exclude", ex])

        # Add tags
        cmd.extend(["--tag", server_conf['name']])
        
        # Add sources 
        sources = [t['path'] for t in targets]
        cmd.extend(sources)
        
        try:
            logger.info(f"Running backup command...")
            # Run from mirror_path so relative sources work
            subprocess.run(cmd, check=True, cwd=mirror_path, env=env)
            logger.info(f"Backup for {server_conf['name']} (Repo: {repo_name}) completed successfully.")
        except subprocess.CalledProcessError as e:
            logger.error(f"Backup failed for {server_conf['name']} (Repo: {repo_name}): {e}")

def prune_repositories(server_conf):
    """Run prune for all repositories of a server."""
    repositories = server_conf.get('repositories', [])
    if not repositories and 'repo_path' in server_conf:
         repositories.append({
            'name': 'default',
            'path': server_conf['repo_path'],
            'password_command': server_conf.get('password_command')
         })

    retention = server_conf.get('retention', {})
    if not retention:
        return

    for repo in repositories:
        repo_path = repo['path']
        repo_name = repo.get('name', 'default')
        
        logger.info(f"Pruning snapshots for {server_conf['name']} (Repo: {repo_name})...")
        
        password_value = get_password_value(repo.get('password_command'))
        if not password_value:
             password_value = get_password_value(server_conf.get('password_command'))

        env = os.environ.copy()
        if password_value:
            env['RESTIC_PASSWORD'] = password_value
            
        # Add repo-specific env
        repo_env = repo.get('env', {})
        for k, v in repo_env.items():
            if v.startswith("op "):
                  try:
                      val = subprocess.check_output(v, shell=True).decode().strip()
                      env[k] = val
                  except:
                      pass
            else:
                  env[k] = v

        cmd = ["restic", "-r", repo_path, "forget", "--prune"]
        
        # Standard policies
        policy_found = False
        for key in ['keep_last', 'keep_daily', 'keep_hourly', 'keep_weekly', 'keep_monthly', 'keep_yearly', 'keep_within']:
            val = retention.get(key)
            if val is not None:
                flag = "--" + key.replace('_', '-')
                cmd.extend([flag, str(val)])
                policy_found = True
        
        if not policy_found:
             # Default
             cmd.extend(["--keep-last", "10"])
             
        try:
            run_command(cmd, env=env)
        except Exception as e:
            logger.error(f"Prune failed for {repo_name}: {e}")


def main():
    if "RESTIC_PASSWORD" not in os.environ:
        logger.info("RESTIC_PASSWORD not set globally. Expecting 'password_command' in server configs.")
    
    # Check for rsync
    if subprocess.call(["which", "rsync"], stdout=subprocess.DEVNULL) != 0:
        logger.error("rsync is not installed. Please install it.")
        sys.exit(1)

    config = load_config(CONFIG_FILE)

    for server in config['servers']:
        try:
            logger.info(f"Processing server: {server['name']}")
            # ensure_repo_init(server) <--- Removed single init, now done per-repo in perform_backup
            
            # 1. Sync files to local mirror
            sync_paths(server)
            
            # 2. Backup from local mirror to ALL repositories
            perform_backup(server)
            
            # 3. Prune ALL repositories
            if server.get('prune', True):
                prune_repositories(server)
            
            # 4. Optional Cleanup
            if server.get('cleanup', False):
                mirror_path = server.get('mirror_path') or server.get('mount_point')
                if mirror_path and os.path.exists(mirror_path):
                    logger.info(f"Cleaning up mirror paths {mirror_path}...")
                    try:
                        shutil.rmtree(mirror_path)
                    except OSError as e:
                        logger.warning(f"Cleanup warning (non-critical): {e}")
                
        except Exception as e:
            logger.error(f"Failed to process {server['name']}: {e}")
            continue

if __name__ == "__main__":
    main()

