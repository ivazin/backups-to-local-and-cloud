export UV_LINK_MODE=copy

.PHONY: backup show-backups help clean

help:
	@echo "🛠️  Available commands:"
	@echo "  make backup        - 🚀 Run backups for all servers"
	@echo "  make show-backups  - 📋 List snapshots for all servers"
	@echo "  make verify        - 🔍 Verify integrity of backups (fast)"
	@echo "  make verify-full   - 🕵️  Verify integrity reading all data (slow)"
	@echo "  make unlock        - 🔓 Remove stale locks from all repositories"
	@echo "  make clean         - 🧹 Remove temporary files and caches"
	@echo "  make clean-cache   - 🗑️  Remove old restic cache files"

clean:
	@echo "🧹 Removing __pycache__ directories..."
	@find . -type d -name "__pycache__" -exec rm -rf {} +
	@echo "🗑️  Removing tmp directories..."
# 	@find . -type d -name "tmp" -exec rm -rf {} +
	@rm -rf ./tmp/ || true

clean-cache:
	@echo "🧹 Cleaning restic cache..."
	restic cache --cleanup

backup: clean
	@echo "🚀 Starting backup process..."
#	to ask for 1password
	@op item get 'Backup Repo Password Restic' --fields password > /dev/null
	
	@uv run --with PyYAML src/backup.py
	@$(MAKE) clean

show-backups: clean
	@echo "📋 Listing all snapshots..."
	uv run --with PyYAML src/restore.py --list all

verify: clean
	@echo "🔍 Verifying backup integrity (fast check)..."
	uv run --with PyYAML src/verify.py

verify-full: clean
	@echo "🕵️  Verifying full data integrity (slow check)..."
	uv run --with PyYAML src/verify.py --full

unlock:
	@echo "🔓 Unlocking repositories..."
	uv run --with PyYAML src/verify.py --unlock

ruff: clean
	@echo "🧹 Cleaning up the code mess..."
	@rm -rf .venv .ruff_cache .mypy_cache
	@uvx ruff check src/*.py --fix
	@uvx ruff format src/*.py
	@uvx mypy src/*.py
	@rm -rf .venv .ruff_cache .mypy_cache