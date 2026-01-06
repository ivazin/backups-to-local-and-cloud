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
        RotatingFileHandler("backup.log", maxBytes=50*1024*1024, backupCount=10),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

def load_config(path):
    with open(path, 'r') as f:
        return yaml.safe_load(f)

def run_command(cmd, env=None, check=True, capture_output=False):
    """Run a shell command."""
    # Don't log full environment as it may contain passwords
    logger.info(f"Running: {' '.join(cmd)}")
    try:
        if capture_output:
             subprocess.run(cmd, check=check, env=env, capture_output=True, text=True)
        else:
             subprocess.run(cmd, check=check, env=env)
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed: {e}")
        if capture_output and e.stderr:
             logger.error(f"Command stderr:\n{e.stderr}")
        if check:
            raise

def get_password(server_conf):
    """Retrieve password from environment or command."""
    # 1. Check for specific password command
    cmd_str = server_conf.get('password_command')
    if cmd_str:
        logger.info(f"Retrieving password using command: {cmd_str}")
        try:
            # We use shell=True to allow complex commands/pipes if user desires,
            result = subprocess.run(cmd_str, shell=True, check=True, capture_output=True, text=True)
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to retrieve password: {e.stderr}")
            raise RuntimeError("Password retrieval failed")

    # 2. Check for global env var
    if "RESTIC_PASSWORD" in os.environ:
        return os.environ["RESTIC_PASSWORD"]
    
    # 3. Fail
    raise ValueError("No password provided (RESTIC_PASSWORD env or password_command in config)")

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
        rsync_log = "rsync_last_run.log"
        
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
    repo_path = os.path.abspath(server_conf['repo_path'])
    
    # Use mirror_path
    mirror_path = server_conf.get('mirror_path') or server_conf.get('mount_point')
    
    # ... rest parsing similar to before ...
    includes = server_conf.get('include', [])
    paths_conf = server_conf.get('paths', [])
    global_excludes = server_conf.get('exclude', [])
    name = server_conf['name']

    current_dir = os.getcwd()
    try:
        os.chdir(mirror_path)
        
        # Normalize targets and path-specific excludes
        targets = []
        for inc in includes:
            targets.append({
                'path': inc.lstrip('/'),
                'excludes': []
            })
        for p_conf in paths_conf:
            p_path = p_conf['path'].lstrip('/')
            p_excludes = p_conf.get('exclude', [])
            targets.append({
                'path': p_path,
                'excludes': p_excludes
            })
            
        if not targets:
            return

        cmd = ["restic", "-r", repo_path, "backup"]
        
        for ex in global_excludes:
            cmd.extend(["--exclude", ex])
            
        for t in targets:
            base_path = t['path']
            for ex in t['excludes']:
                if ex.startswith('/'):
                    final_ex = ex
                else:
                    final_ex = os.path.join(base_path, ex)
                    if not final_ex.startswith('/'):
                         final_ex = '/' + final_ex
                cmd.extend(["--exclude", final_ex])

        cmd.extend(["--tag", name])
        
        for t in targets:
            cmd.append(t['path'])

        logger.info(f"Starting restic backup for {name} from {mirror_path}...")
        
        env = os.environ.copy()
        env['RESTIC_PASSWORD'] = get_password(server_conf)
        
        run_command(cmd, env=env)
        logger.info(f"Backup for {name} completed successfully.")
        
    except Exception as e:
        logger.error(f"Backup failed for {name}: {e}")
        raise
    finally:
        os.chdir(current_dir)

def prune_repository(server_conf):
    repo_path = os.path.abspath(server_conf['repo_path'])
    name = server_conf['name']
    
    retention = server_conf.get('retention', {})
    
    # Build forget command
    cmd = ["restic", "-r", repo_path, "forget", "--prune"]
    
    # Supported keys mapping (config_key -> flag)
    # We allow keys like keep_last, keep_daily, etc.
    # If users use dashes in yaml "keep-last", we handle that too if we want, 
    # but python dict keys usually underscores. Use underscores in config.
    policy_found = False
    
    # Standard policies
    for key in ['keep_last', 'keep_daily', 'keep_hourly', 'keep_weekly', 'keep_monthly', 'keep_yearly', 'keep_within']:
        val = retention.get(key)
        if val is not None:
            # Convert keep_daily -> --keep-daily
            flag = "--" + key.replace('_', '-')
            cmd.extend([flag, str(val)])
            policy_found = True
            
    # Fallback if no retention block or keys found: default keep-last 10
    if not policy_found:
        # Check root level legacy
        legacy_val = server_conf.get('keep_last')
        if legacy_val:
             cmd.extend(["--keep-last", str(legacy_val)])
        else:
             # Default
             cmd.extend(["--keep-last", "10"])

    logger.info(f"Pruning snapshots for {name} with policy...")
    
    try:
        env = os.environ.copy()
        env['RESTIC_PASSWORD'] = get_password(server_conf)
        run_command(cmd, env=env)
    except Exception as e:
        logger.warning(f"Prune failed for {name}: {e}")


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
            ensure_repo_init(server)
            
            # 1. Sync files to local mirror
            sync_paths(server)
            
            # 2. Backup from local mirror
            perform_backup(server)
            
            # 3. Prune
            if server.get('prune', True):
                prune_repository(server)
            
            # 4. Optional Cleanup
            if server.get('cleanup', False):
                mirror_path = server.get('mirror_path') or server.get('mount_point')
                if mirror_path and os.path.exists(mirror_path):
                    logger.info(f"Cleaning up mirror paths {mirror_path}...")
                    shutil.rmtree(mirror_path)
                
        except Exception as e:
            logger.error(f"Failed to process {server['name']}: {e}")
            continue

if __name__ == "__main__":
    main()

