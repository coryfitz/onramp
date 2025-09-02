"""
Migration management for OnRamp using Aerich
"""
import os
import sys
import subprocess
import asyncio
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
        
        # Check if aerich config exists in pyproject.toml
        if os.path.exists(aerich_config_path):
            with open(aerich_config_path, 'r') as f:
                content = f.read()
                if '[tool.aerich]' in content:
                    return  # Already configured
        
        # Create or update pyproject.toml with aerich config
        tortoise_config = self.db_manager.get_tortoise_config()
        
        aerich_section = f"""
[tool.aerich]
tortoise_orm = "app.db.db_config.TORTOISE_ORM"
location = "./app/db/migrations"
src_folder = "./."
"""
        
        # Create db_config.py in app/db/ directory for aerich to import
        db_config_path = os.path.join(self.db_dir, 'db_config.py')
        with open(db_config_path, 'w') as f:
            f.write(f"""# Auto-generated database config for aerich
TORTOISE_ORM = {repr(tortoise_config)}
""")
        
        # Create __init__.py in db directory to make it a package
        init_path = os.path.join(self.db_dir, '__init__.py')
        with open(init_path, 'w') as f:
            f.write("# Database package\n")
        
        if os.path.exists(aerich_config_path):
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
        if cwd is None:
            cwd = self.project_root
            
        full_command = [sys.executable, "-m", "aerich"] + command
        
        try:
            result = subprocess.run(
                full_command,
                cwd=cwd,
                capture_output=True,
                text=True,
                check=True
            )
            print(result.stdout)
            if result.stderr:
                print(result.stderr)
            return True
        except subprocess.CalledProcessError as e:
            print(f"Error running aerich {' '.join(command)}: {e}")
            print(f"stdout: {e.stdout}")
            print(f"stderr: {e.stderr}")
            return False
    
    def init_migrations(self):
        """Initialize aerich (first time setup)"""
        print("Setting up migration system...")
        self._ensure_aerich_config()
        
        migrations_dir = os.path.join(self.db_dir, 'migrations')
        if not os.path.exists(migrations_dir):
            # Try to run aerich init-db, but don't fail if it doesn't work
            # (might fail if models aren't properly set up yet)
            try:
                return self._run_aerich_command(["init-db"])
            except Exception as e:
                print(f"Note: Initial migration setup will complete when you first run 'onramp migrate'")
                return True  # Don't fail app creation
        else:
            print("Migration system already initialized")
            return True
    
    def create_migration(self, name: Optional[str] = None):
        """Create a new migration (equivalent to Django's makemigrations)"""
        print("Creating migration...")
        self._ensure_aerich_config()
        
        # Make sure aerich is initialized first
        if not os.path.exists(os.path.join(self.db_dir, 'migrations')):
            self.init_migrations()
        
        command = ["migrate"]
        if name:
            command.extend(["--name", name])
        
        return self._run_aerich_command(command)
    
    def apply_migrations(self):
        """Apply pending migrations (equivalent to Django's migrate)"""
        print("Applying migrations...")
        self._ensure_aerich_config()
        
        # Make sure aerich is initialized first
        if not os.path.exists(os.path.join(self.db_dir, 'migrations')):
            self.init_migrations()
            return True  # init-db also applies initial schema
        
        return self._run_aerich_command(["upgrade"])
    
    def migrate_with_prep(self, name: Optional[str] = None):
        """Create and apply migrations in one go"""
        print("Preparing and applying migrations...")
        
        # Check if this is the first time - if so, initialize
        migrations_dir = os.path.join(self.db_dir, 'migrations')
        if not os.path.exists(migrations_dir):
            print("First time setup - initializing migration system...")
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
    if _migration_manager is None:
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

def init_migrations(app_dir: str = None):
    """Initialize migration system (internal use only)"""
    manager = get_migration_manager(app_dir)
    return manager.init_migrations()