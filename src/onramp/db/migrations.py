"""
Migration management for OnRamp using Aerich
"""
import os
import sys
import subprocess
from typing import Optional
from .manager import get_db_manager

class MigrationManager:
    """Manages database migrations using Aerich"""
    
    def __init__(self, app_dir: str = None):
        self.app_dir = app_dir
        self.db_manager = get_db_manager(app_dir)
        self.project_root = os.path.dirname(self.db_manager.app_dir)
        self.db_dir = os.path.join(self.db_manager.app_dir, 'db')  # app/db/
        
    def _ensure_aerich_config(self):
        """Ensure aerich.toml exists with proper configuration"""
        aerich_config_path = os.path.join(self.project_root, 'pyproject.toml')
        
        # Ensure the db directory exists
        os.makedirs(self.db_dir, exist_ok=True)
        
        aerich_section = f"""
[tool.aerich]
tortoise_orm = "app.db.db_config.TORTOISE_ORM"
location = "./app/db/migrations"
src_folder = "./."
"""
        
        # Create db_config.py in app/db/ directory for aerich to import
        db_config_path = os.path.join(self.db_dir, 'db_config.py')
        if not os.path.exists(db_config_path):
            with open(db_config_path, 'w') as f:
                f.write("""# Auto-generated database config for Aerich.
# Resolve the app directory at import time so cloned or moved projects remain portable.
from pathlib import Path

from onramp.db.manager import DatabaseManager

APP_DIR = Path(__file__).resolve().parents[1]
TORTOISE_ORM = DatabaseManager(str(APP_DIR)).get_tortoise_config()
""")
        
        # Create __init__.py in db directory to make it a package
        init_path = os.path.join(self.db_dir, '__init__.py')
        if not os.path.exists(init_path):
            with open(init_path, 'w') as f:
                f.write("# Database package\n")
        
        if os.path.exists(aerich_config_path):
            with open(aerich_config_path, 'r', encoding='utf-8') as f:
                content = f.read()
            if '[tool.aerich]' in content:
                return
            with open(aerich_config_path, 'a') as f:
                f.write(aerich_section)
        else:
            with open(aerich_config_path, 'w') as f:
                f.write(f"""[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[project]
name = "onramp-app"
version = "0.1.0"
{aerich_section}""")
    
    def _run_aerich_command(self, command: list, cwd: str = None):
        """Run an aerich command"""
        result = self._run_aerich_command_result(command, cwd=cwd)
        if result.returncode == 0:
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(result.stderr)
            return True

        print(f"Error running aerich {' '.join(command)}")
        if result.stdout:
            print(f"stdout: {result.stdout}")
        if result.stderr:
            print(f"stderr: {result.stderr}")
        return False

    def _run_aerich_command_result(self, command: list, cwd: str = None):
        """Run Aerich and return its completed process for inspection."""
        if cwd is None:
            cwd = self.project_root
            
        full_command = [sys.executable, "-m", "aerich"] + command
        
        return subprocess.run(
            full_command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    
    def init_migrations(self):
        """Prepare Aerich without binding a new project to a database dialect."""
        print("Setting up migration system...")
        self._ensure_aerich_config()
        migrations_dir = os.path.join(self.db_dir, 'migrations')
        if os.path.exists(migrations_dir):
            print("Migration system already initialized")
        else:
            print(
                "Migration configuration ready. The first 'onramp migrate' "
                "will create migrations for the configured database engine."
            )
        return True
    
    def create_migration(self, name: Optional[str] = None):
        """Create a new migration"""
        if self.db_manager.environment() != "development":
            print(
                "Migration creation is available only in development. "
                "Use 'onramp db upgrade' to apply committed migrations."
            )
            return False
        print("Creating migration...")
        self._ensure_aerich_config()
        
        migrations_dir = os.path.join(self.db_dir, 'migrations')
        if not os.path.exists(migrations_dir):
            if name:
                print(
                    "The first migration uses Aerich's initial migration name; "
                    "the supplied name will apply to later migrations."
                )
            return self._run_aerich_command(["init-migrations"])

        command = ["migrate", "--offline"]
        if name:
            command.extend(["--name", name])
        
        return self._run_aerich_command(command)
    
    def apply_migrations(self):
        """Apply pending migrations"""
        print("Applying migrations...")
        self.db_manager.validate_runtime_configuration()
        self._ensure_aerich_config()
        
        # Make sure aerich is initialized first
        if not os.path.exists(os.path.join(self.db_dir, 'migrations')):
            print(
                "No committed migrations were found. Run 'onramp migrate' in "
                "development against the same database engine used in production."
            )
            return False
        
        return self._run_aerich_command(["upgrade"])

    def check_migrations(self):
        """Report whether the configured database has pending migrations."""
        self._ensure_aerich_config()
        migrations_dir = os.path.join(self.db_dir, 'migrations')
        if not os.path.exists(migrations_dir):
            print("No migration directory was found.")
            return False

        result = self._run_aerich_command_result(["heads"])
        if result.returncode != 0:
            print("Could not check database migrations.")
            if result.stderr:
                print(result.stderr)
            return False

        output = result.stdout.strip()
        if not output or output == "No available heads.":
            print("Database migrations are up to date.")
            return True

        print("Pending database migrations:")
        print(output)
        return False
    
    def migrate_with_prep(self, name: Optional[str] = None):
        """Create and apply migrations in one go"""
        if self.db_manager.environment() != "development":
            print(
                "'onramp migrate' creates migration files and is available only "
                "in development. Use 'onramp db upgrade' in deployments."
            )
            return False
        print("Preparing and applying migrations...")
        self._ensure_aerich_config()
        
        # Check if this is the first time - if so, initialize
        migrations_dir = os.path.join(self.db_dir, 'migrations')
        if not os.path.exists(migrations_dir):
            print("First time setup - initializing migration system...")
            if name:
                print(
                    "The first migration uses Aerich's initial migration name; "
                    "the supplied name will apply to later migrations."
                )
            if not self._run_aerich_command(["init-db"]):
                return False
            print("Migration system initialized and initial schema created")
            return True
        
        # Otherwise, create migration then apply
        if self.create_migration(name):
            # Then apply it
            return self.apply_migrations()
        return False

# Global migration manager
_migration_manager = None

def get_migration_manager(app_dir: str = None):
    """Get or create migration manager instance"""
    global _migration_manager
    requested_app_dir = os.path.abspath(app_dir) if app_dir else None
    current_app_dir = (
        os.path.abspath(_migration_manager.app_dir)
        if _migration_manager is not None and _migration_manager.app_dir
        else None
    )
    if _migration_manager is None or (
        requested_app_dir is not None and requested_app_dir != current_app_dir
    ):
        _migration_manager = MigrationManager(app_dir)
    return _migration_manager

def create_migration(name: Optional[str] = None, app_dir: str = None):
    """Create a new migration"""
    manager = get_migration_manager(app_dir)
    return manager.create_migration(name)

def migrate(name: Optional[str] = None, app_dir: str = None):
    """Apply migrations (with optional prep step)"""
    manager = get_migration_manager(app_dir)
    return manager.migrate_with_prep(name)


def apply_migrations(app_dir: str = None):
    """Apply existing migrations without generating new files."""
    manager = get_migration_manager(app_dir)
    return manager.apply_migrations()


def check_migrations(app_dir: str = None):
    """Report whether existing migrations have all been applied."""
    manager = get_migration_manager(app_dir)
    return manager.check_migrations()

def init_migrations(app_dir: str = None):
    """Initialize migration system (internal use only)"""
    manager = get_migration_manager(app_dir)
    return manager.init_migrations()
