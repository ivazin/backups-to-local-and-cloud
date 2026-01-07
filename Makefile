.PHONY: backup show-backups help clean

help:
	@echo "Available commands:"
	@echo "  make backup        - Run backups for all servers"
	@echo "  make show-backups  - List snapshots for all servers"
	@echo "  make clean         - Remove __pycache__"
	@echo "  make clean-cache   - Remove old restic cache files"

clean:
	@echo "###### Removing __pycache__ directories... ######"
	@find . -type d -name "__pycache__" -exec rm -rf {} +
	@echo "###### Removing tmp directories... ######"
# 	@find . -type d -name "tmp" -exec rm -rf {} +
	@rm -rf ./tmp/

clean-cache:
	restic cache --cleanup

backup: clean
	@echo "###### Running backup... ######"
#	to ask for 1password
	@op item get 'Backup Repo Password Restic' --fields password
	
	@uv run --with PyYAML backup.py
	@$(MAKE) clean

show-backups: clean
	uv run --with PyYAML restore.py --list all
