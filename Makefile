.PHONY: backup show-backups help clean

help:
	@echo "Available commands:"
	@echo "  make backup        - Run backups for all servers"
	@echo "  make show-backups  - List snapshots for all servers"
	@echo "  make clean         - Remove __pycache__"

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +

backup: clean
	uv run --with PyYAML backup.py

show-backups: clean
	uv run --with PyYAML restore.py --list all
