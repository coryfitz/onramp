from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

BACKEND = False

# Runtime environment. ONRAMP_ENVIRONMENT can override this at launch.
ENVIRONMENT = 'development'

# Convenient for local development only. OnRamp ignores this setting unless
# ENVIRONMENT (or ONRAMP_ENVIRONMENT) is exactly "development". Use Aerich
# migrations to prepare non-development databases.
AUTO_GENERATE_SCHEMAS = True

# Database configuration. DATABASE_URL takes precedence when it is present in
# the process environment, so production credentials never need to be stored in
# this file. The structured values remain convenient local-development defaults.
DATABASE = {
    'engine': 'sqlite',        # sqlite, postgresql, mysql
    'name': 'db.sqlite3',      # Database name (or path for SQLite)
    'host': 'localhost',       # Database host (ignored for SQLite)
    'port': None,              # Database port (ignored for SQLite) 
    'user': '',                # Database user (ignored for SQLite)
    'password': '',            # Database password (ignored for SQLite)
}

# Optional connection-pool settings. Each value can also be overridden with an
# ONRAMP_DATABASE_* environment variable; see the generated project README.
DATABASE_OPTIONS = {
    'min_size': 1,
    'max_size': 5,
    'connect_timeout': 10,
    # 'ssl': True,
}

# HTTP deployment settings. Environment variables accept comma-separated lists.
ALLOWED_HOSTS = ['*']
CORS_ALLOWED_ORIGINS = []
CORS_ALLOW_CREDENTIALS = False

# For PostgreSQL:
# DATABASE = {
#     'engine': 'postgresql',
#     'name': 'myapp_db',
#     'host': 'localhost',
#     'port': 5432,
#     'user': 'myuser',
#     'password': 'mypassword',
# }

# For MySQL:
# DATABASE = {
#     'engine': 'mysql',
#     'name': 'myapp_db',
#     'host': 'localhost', 
#     'port': 3306,
#     'user': 'myuser',
#     'password': 'mypassword',
# }

# Add more non-secret settings as needed. Keep passwords, API keys, and signing
# material in the deployment provider's secret environment.
