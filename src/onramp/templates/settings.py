from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

BACKEND = False

# Database Configuration
DATABASE = {
    'engine': 'sqlite',        # sqlite, postgresql, mysql
    'name': 'db.sqlite3',      # Database name (or path for SQLite)
    'host': 'localhost',       # Database host (ignored for SQLite)
    'port': None,              # Database port (ignored for SQLite) 
    'user': '',                # Database user (ignored for SQLite)
    'password': '',            # Database password (ignored for SQLite)
}

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

# Add more settings as needed