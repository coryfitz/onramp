"""
Database connection and management for OnRamp
"""
from contextlib import asynccontextmanager
import importlib.util
import os
import sys
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from tortoise import Tortoise


def _env_bool(name: str, default: bool | None = None) -> bool | None:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _env_int(name: str, default=None):
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


def _comma_separated(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = value.split(",")
    else:
        values = value
    return [str(item).strip() for item in values if str(item).strip()]

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
                },
                'DATABASE_OPTIONS': {},
                'ALLOWED_HOSTS': ['*'],
                'CORS_ALLOWED_ORIGINS': [],
                'CORS_ALLOW_CREDENTIALS': False,
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
            'DATABASE_OPTIONS': getattr(settings_module, 'DATABASE_OPTIONS', {}),
            'ALLOWED_HOSTS': getattr(settings_module, 'ALLOWED_HOSTS', ['*']),
            'CORS_ALLOWED_ORIGINS': getattr(
                settings_module,
                'CORS_ALLOWED_ORIGINS',
                [],
            ),
            'CORS_ALLOW_CREDENTIALS': getattr(
                settings_module,
                'CORS_ALLOW_CREDENTIALS',
                False,
            ),
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

    def allowed_hosts(self) -> list[str]:
        """Return trusted HTTP hosts, with an environment override."""
        return _comma_separated(
            os.environ.get(
                "ONRAMP_ALLOWED_HOSTS",
                self.settings.get("ALLOWED_HOSTS", ["*"]),
            )
        ) or ["*"]

    def cors_allowed_origins(self) -> list[str]:
        """Return browser origins allowed to call this backend."""
        return _comma_separated(
            os.environ.get(
                "ONRAMP_CORS_ALLOWED_ORIGINS",
                self.settings.get("CORS_ALLOWED_ORIGINS", []),
            )
        )

    def cors_allow_credentials(self) -> bool:
        """Return whether configured CORS origins may send credentials."""
        return bool(
            _env_bool(
                "ONRAMP_CORS_ALLOW_CREDENTIALS",
                bool(self.settings.get("CORS_ALLOW_CREDENTIALS", False)),
            )
        )

    def _database_config(self) -> dict:
        """Resolve structured database settings with environment overrides."""
        config = dict(self.settings.get('DATABASE', {}))
        overrides = {
            'engine': os.environ.get('ONRAMP_DATABASE_ENGINE'),
            'name': os.environ.get('ONRAMP_DATABASE_NAME'),
            'host': os.environ.get('ONRAMP_DATABASE_HOST'),
            'port': _env_int('ONRAMP_DATABASE_PORT'),
            'user': os.environ.get('ONRAMP_DATABASE_USER'),
            'password': os.environ.get('ONRAMP_DATABASE_PASSWORD'),
        }
        for key, value in overrides.items():
            if value is not None:
                config[key] = value
        return config

    def _database_options(self, engine: str) -> dict:
        """Resolve safe URL-compatible driver and pool options."""
        if engine not in {'postgresql', 'mysql'}:
            return {}
        options = dict(self.settings.get('DATABASE_OPTIONS', {}))
        minimum = _env_int(
            'ONRAMP_DATABASE_POOL_MIN_SIZE',
            options.get('min_size'),
        )
        maximum = _env_int(
            'ONRAMP_DATABASE_POOL_MAX_SIZE',
            options.get('max_size'),
        )
        timeout = _env_int(
            'ONRAMP_DATABASE_CONNECT_TIMEOUT',
            options.get('connect_timeout'),
        )
        ssl_enabled = _env_bool(
            'ONRAMP_DATABASE_SSL',
            options.get('ssl'),
        )

        resolved = {}
        if minimum is not None:
            resolved['min_size' if engine == 'postgresql' else 'minsize'] = minimum
        if maximum is not None:
            resolved['max_size' if engine == 'postgresql' else 'maxsize'] = maximum
        if timeout is not None:
            resolved['timeout' if engine == 'postgresql' else 'connect_timeout'] = timeout
        if ssl_enabled:
            resolved['ssl'] = 'true'
        return resolved

    @staticmethod
    def _append_url_options(database_url: str, options: dict) -> str:
        if not options:
            return database_url
        parts = urlsplit(database_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        for key, value in options.items():
            query.setdefault(key, str(value))
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )

    def _configured_database_url(self, db_config: dict | None = None) -> str:
        config = db_config or self._database_config()
        url_env = config.get('url_env', 'DATABASE_URL')
        return (
            os.environ.get(str(url_env), '').strip()
            or str(config.get('url', '')).strip()
        )
    
    def _get_database_url(self):
        """Generate database URL from settings"""
        db_config = self._database_config()
        engine = db_config.get('engine', 'sqlite').lower()

        database_url = self._configured_database_url(db_config)
        if database_url:
            url_engine = urlsplit(database_url).scheme.lower()
            normalized_engine = {
                'postgres': 'postgresql',
                'postgresql': 'postgresql',
                'asyncpg': 'postgresql',
                'mysql': 'mysql',
                'sqlite': 'sqlite',
            }.get(url_engine, url_engine)
            return self._append_url_options(
                database_url,
                self._database_options(normalized_engine),
            )
        
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
            port = db_config.get('port') or 5432
            user = db_config.get('user', '')
            password = db_config.get('password', '')
            name = db_config.get('name', '')
            database_url = (
                f"postgres://{quote(str(user), safe='')}:{quote(str(password), safe='')}"
                f"@{host}:{port}/{quote(str(name), safe='')}"
            )
            return self._append_url_options(
                database_url,
                self._database_options(engine),
            )
        
        elif engine == 'mysql':
            host = db_config.get('host', 'localhost')
            port = db_config.get('port') or 3306
            user = db_config.get('user', '')
            password = db_config.get('password', '')
            name = db_config.get('name', '')
            database_url = (
                f"mysql://{quote(str(user), safe='')}:{quote(str(password), safe='')}"
                f"@{host}:{port}/{quote(str(name), safe='')}"
            )
            return self._append_url_options(
                database_url,
                self._database_options(engine),
            )
        
        else:
            raise ValueError(f"Unsupported database engine: {engine}")

    def _get_database_connection(self):
        """Return a Tortoise connection without URL-parsing structured secrets."""
        db_config = self._database_config()
        if self._configured_database_url(db_config):
            return self._get_database_url()

        engine = str(db_config.get('engine', 'sqlite')).lower()
        if engine == 'sqlite':
            return self._get_database_url()
        if engine not in {'postgresql', 'mysql'}:
            return self._get_database_url()

        options = self._database_options(engine)
        credentials = {
            'database': db_config.get('name', ''),
            'host': db_config.get('host', 'localhost'),
            'port': db_config.get('port') or (5432 if engine == 'postgresql' else 3306),
            'user': db_config.get('user', ''),
            'password': db_config.get('password', ''),
        }
        credentials.update(options)
        if engine == 'postgresql':
            if 'min_size' in credentials:
                credentials['minsize'] = credentials.pop('min_size')
            if 'max_size' in credentials:
                credentials['maxsize'] = credentials.pop('max_size')
            if credentials.get('ssl') == 'true':
                credentials['ssl'] = True
            return {
                'engine': 'tortoise.backends.asyncpg',
                'credentials': credentials,
            }
        if credentials.get('ssl') == 'true':
            credentials['ssl'] = True
        return {
            'engine': 'tortoise.backends.mysql',
            'credentials': credentials,
        }

    def database_description(self) -> str:
        """Describe the configured database without exposing credentials."""
        database_url = self._get_database_url()
        parts = urlsplit(database_url)
        engine = {
            'postgres': 'postgresql',
            'asyncpg': 'postgresql',
        }.get(parts.scheme.lower(), parts.scheme.lower())
        if engine == 'sqlite':
            return f"sqlite ({parts.path or parts.netloc})"
        database = parts.path.lstrip('/') or '(default database)'
        host = parts.hostname or '(default host)'
        if parts.port:
            host = f"{host}:{parts.port}"
        return f"{engine} ({host}/{database})"

    def uses_sqlite(self) -> bool:
        return urlsplit(self._get_database_url()).scheme.lower() == 'sqlite'

    def validate_runtime_configuration(self) -> None:
        """Reject ephemeral production defaults unless explicitly accepted."""
        allow_sqlite = bool(_env_bool('ONRAMP_ALLOW_PRODUCTION_SQLITE', False))
        if self.environment() == 'production' and self.uses_sqlite() and not allow_sqlite:
            raise RuntimeError(
                "Production is configured to use SQLite. Set DATABASE_URL to a "
                "managed database, or explicitly set "
                "ONRAMP_ALLOW_PRODUCTION_SQLITE=true when persistent SQLite "
                "storage is intentional."
            )
    
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
                "default": self._get_database_connection()
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
_db_initialized = False
_db_connection = None

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
    manager.validate_runtime_configuration()
    config = manager.get_tortoise_config()
    
    global _db_connection, _db_initialized
    await Tortoise.init(config)
    _db_initialized = True
    get_connection = getattr(Tortoise, "get_connection", None)
    _db_connection = get_connection("default") if get_connection else None
    print(f"Database initialized: {manager.database_description()}")

async def close_db():
    """Close database connections"""
    global _db_connection, _db_initialized
    await Tortoise.close_connections()
    _db_initialized = False
    _db_connection = None
    print("Database connections closed")


async def database_is_ready() -> bool:
    """Return whether the initialized default connection can answer a query."""
    if not _db_initialized:
        return False
    try:
        connection = _db_connection or Tortoise.get_connection("default")
        await connection.execute_query("SELECT 1")
        return True
    except Exception:
        return False

def database_lifespan(app_dir: str = None):
    """Create a Starlette lifespan that owns the database connection."""
    manager = get_db_manager(app_dir)

    @asynccontextmanager
    async def lifespan(_app):
        global _db_connection, _db_initialized
        initialized = False
        try:
            manager.validate_runtime_configuration()
            await Tortoise.init(config=manager.get_tortoise_config())
            initialized = True
            _db_initialized = True
            get_connection = getattr(Tortoise, "get_connection", None)
            _db_connection = get_connection("default") if get_connection else None
            if manager.should_generate_schemas():
                await Tortoise.generate_schemas()
            print(f"Database initialized: {manager.database_description()}")
            yield
        finally:
            if initialized:
                await Tortoise.close_connections()
                _db_initialized = False
                _db_connection = None
                print("Database connections closed")

    return lifespan
