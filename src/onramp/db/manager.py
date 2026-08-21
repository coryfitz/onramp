"""
Database connection and management for OnRamp
"""
from contextlib import asynccontextmanager
import importlib.util
import os
import sys
from tortoise import Tortoise

class DatabaseManager:
    """Manages database connections and model discovery"""
    
    def __init__(self, app_dir: str = None):
        self.app_dir = app_dir or self._find_app_directory()
        self.settings = self._load_settings()
        self.models = []
        
    def _find_app_directory(self):
        """Find the app directory"""
        current_dir = os.getcwd()
        
        # Try app/ subdirectory first
        app_dir = os.path.join(current_dir, 'app')
        if os.path.exists(app_dir):
            return app_dir
            
        # If we're already in app directory
        if os.path.exists(os.path.join(current_dir, 'settings.py')):
            return current_dir
            
        return current_dir
    
    def _load_settings(self):
        """Load settings from app/settings.py"""
        settings_path = os.path.join(self.app_dir, 'settings.py')
        
        if not os.path.exists(settings_path):
            # Return default settings
            return {
                'ENVIRONMENT': 'development',
                'AUTO_GENERATE_SCHEMAS': True,
                'DATABASE': {
                    'engine': 'sqlite',
                    'name': 'db.sqlite3',
                    'host': 'localhost',
                    'port': None,
                    'user': '',
                    'password': '',
                }
            }
        
        # Import settings module
        spec = importlib.util.spec_from_file_location("app_settings", settings_path)
        settings_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(settings_module)
        
        # Extract database settings
        database_config = getattr(settings_module, 'DATABASE', {
            'engine': 'sqlite',
            'name': 'db.sqlite3',
        })

        return {
            'ENVIRONMENT': getattr(
                settings_module,
                'ENVIRONMENT',
                'development',
            ),
            'AUTO_GENERATE_SCHEMAS': getattr(
                settings_module,
                'AUTO_GENERATE_SCHEMAS',
                True,
            ),
            'DATABASE': database_config,
        }

    def environment(self):
        """Return the normalized runtime environment name."""
        value = os.environ.get(
            'ONRAMP_ENVIRONMENT',
            self.settings.get('ENVIRONMENT', 'development'),
        )
        return str(value).strip().lower()

    def should_generate_schemas(self):
        """Allow automatic schema creation only during development."""
        return (
            self.environment() == 'development'
            and bool(self.settings.get('AUTO_GENERATE_SCHEMAS', True))
        )
    
    def _get_database_url(self):
        """Generate database URL from settings"""
        db_config = self.settings['DATABASE']
        engine = db_config.get('engine', 'sqlite').lower()
        
        if engine == 'sqlite':
            db_name = db_config.get('name', 'db.sqlite3')
            # Put SQLite database in app/db/ directory
            if not os.path.isabs(db_name):
                db_dir = os.path.join(self.app_dir, 'db')
                os.makedirs(db_dir, exist_ok=True)
                db_path = os.path.join(db_dir, db_name)
            else:
                db_path = db_name
            return f"sqlite://{db_path}"
        
        elif engine == 'postgresql':
            host = db_config.get('host', 'localhost')
            port = db_config.get('port', 5432)
            user = db_config.get('user', '')
            password = db_config.get('password', '')
            name = db_config.get('name', '')
            return f"postgres://{user}:{password}@{host}:{port}/{name}"
        
        elif engine == 'mysql':
            host = db_config.get('host', 'localhost')
            port = db_config.get('port', 3306)
            user = db_config.get('user', '')
            password = db_config.get('password', '')
            name = db_config.get('name', '')
            return f"mysql://{user}:{password}@{host}:{port}/{name}"
        
        else:
            raise ValueError(f"Unsupported database engine: {engine}")
    
    def discover_models(self):
        """Discover all model classes in the app"""
        models_path = os.path.join(self.app_dir, 'models')
        
        # Add both the app directory and project root to Python path
        project_root = os.path.dirname(self.app_dir)
        if self.app_dir not in sys.path:
            sys.path.insert(0, self.app_dir)
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        
        model_modules = []
        
        # Check for models.py file in models directory
        models_file = os.path.join(models_path, 'models.py')
        if os.path.exists(models_file):
            model_modules.append('app.models.models')
        
        # Check for individual model files in models directory
        if os.path.exists(models_path):
            for filename in os.listdir(models_path):
                if filename.endswith('.py') and not filename.startswith('__'):
                    module_name = filename[:-3]
                    if module_name != 'models':  # Don't duplicate models.models
                        model_modules.append(f'app.models.{module_name}')
        
        return model_modules
    
    def get_tortoise_config(self):
        """Get Tortoise ORM configuration"""
        return {
            "connections": {
                "default": self._get_database_url()
            },
            "apps": {
                "models": {
                    "models": self.discover_models() + ["aerich.models"],
                    "default_connection": "default",
                }
            }
        }

# Global database manager instance
_db_manager = None

def get_db_manager(app_dir: str = None):
    """Get or create database manager instance"""
    global _db_manager
    requested_app_dir = os.path.abspath(app_dir) if app_dir else None
    current_app_dir = (
        os.path.abspath(_db_manager.app_dir)
        if _db_manager is not None and _db_manager.app_dir
        else None
    )
    if _db_manager is None or (
        requested_app_dir is not None and requested_app_dir != current_app_dir
    ):
        _db_manager = DatabaseManager(app_dir)
    return _db_manager

async def init_db(app_dir: str = None):
    """Initialize database connection"""
    manager = get_db_manager(app_dir)
    config = manager.get_tortoise_config()
    
    await Tortoise.init(config)
    print(f"Database initialized: {manager._get_database_url()}")

async def close_db():
    """Close database connections"""
    await Tortoise.close_connections()
    print("Database connections closed")

def database_lifespan(app_dir: str = None):
    """Create a Starlette lifespan that owns the database connection."""
    manager = get_db_manager(app_dir)

    @asynccontextmanager
    async def lifespan(_app):
        initialized = False
        try:
            await Tortoise.init(config=manager.get_tortoise_config())
            initialized = True
            if manager.should_generate_schemas():
                await Tortoise.generate_schemas()
            print(f"Database initialized: {manager._get_database_url()}")
            yield
        finally:
            if initialized:
                await Tortoise.close_connections()
                print("Database connections closed")

    return lifespan
