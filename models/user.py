"""
User Model for Flask-Login Authentication

This module provides the User model that integrates Flask-Login with PostgREST,
with direct psycopg2 fallback for resilience. When PostgREST is overloaded or
unavailable, auth lookups fall back to direct PostgreSQL queries so that user
sessions remain valid even under heavy system load.

Classes:
    User: Flask-Login user model with PostgREST + psycopg2 fallback
"""

from flask_login import UserMixin
import requests
import os

# PostgREST connection URL (containerized service)
POSTGREST_URL = os.getenv('NVR_POSTGREST_URL', 'http://postgrest:3001')

# Direct PostgreSQL connection parameters (used as fallback when PostgREST is unavailable)
_PG_HOST = os.environ.get('POSTGRES_HOST', 'postgres')
_PG_PORT = int(os.environ.get('POSTGRES_PORT', '5432'))
_PG_DB = os.environ.get('POSTGRES_DB', 'nvr')
_PG_USER = os.environ.get('POSTGRES_USER', 'nvr_api')
_PG_PASS = os.environ.get('POSTGRES_PASSWORD', '')


def _direct_query(sql, params=None, fetch_one=False):
    """
    Execute a direct SQL query against PostgreSQL using psycopg2.
    Used as fallback when PostgREST is unavailable.

    Args:
        sql (str): SQL query to execute
        params (tuple, optional): Query parameters
        fetch_one (bool): If True, return single row dict; if False, return list of dicts

    Returns:
        dict or list: Query results as dict(s) with column names as keys, or None on error
    """
    try:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(
            host=_PG_HOST, port=_PG_PORT, dbname=_PG_DB,
            user=_PG_USER, password=_PG_PASS, connect_timeout=5
        )
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                if fetch_one:
                    row = cur.fetchone()
                    return dict(row) if row else None
                return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as e:
        print(f"[User model] Direct SQL fallback failed: {e}")
        return None


class User(UserMixin):
    """
    User model for Flask-Login authentication.

    Integrates with PostgREST to load user data from PostgreSQL database.
    Supports two roles: 'admin' and 'user'.

    Attributes:
        id (int): Unique user identifier (primary key)
        username (str): Unique username for login
        role (str): User role ('admin' or 'user')
        must_change_password (bool): Force password change on next login
    """

    def __init__(self, id, username, role, must_change_password=False):
        """
        Initialize a User instance.

        Args:
            id (int): User ID from database
            username (str): Username
            role (str): User role ('admin' or 'user')
            must_change_password (bool, optional): Password change required flag. Defaults to False.
        """
        self.id = id
        self.username = username
        self.role = role
        self.must_change_password = must_change_password

    @staticmethod
    def get_by_id(user_id):
        """
        Load user by ID from database.

        Primary path: PostgREST REST API (lightweight, uses connection pooling).
        Fallback: Direct psycopg2 query (resilient to PostgREST saturation).

        This is called on EVERY authenticated request by Flask-Login's
        @login_manager.user_loader. If this returns None, the session is
        invalidated - so resilience here is critical.

        Args:
            user_id (int): User ID to look up

        Returns:
            User: User instance if found, None otherwise
        """
        # Primary path: PostgREST
        try:
            response = requests.get(
                f"{POSTGREST_URL}/users",
                params={'id': f'eq.{user_id}'},
                timeout=3
            )

            if response.status_code == 200:
                users = response.json()
                if users:
                    data = users[0]
                    return User(
                        id=data['id'],
                        username=data['username'],
                        role=data['role'],
                        must_change_password=data['must_change_password']
                    )
        except requests.RequestException as e:
            print(f"[User.get_by_id] PostgREST failed for user {user_id}: {e}, trying direct SQL...")

            # Fallback: direct psycopg2 query
            data = _direct_query(
                "SELECT id, username, role, must_change_password FROM users WHERE id = %s",
                (user_id,), fetch_one=True
            )
            if data:
                return User(
                    id=data['id'],
                    username=data['username'],
                    role=data['role'],
                    must_change_password=data['must_change_password']
                )

        return None

    @staticmethod
    def get_by_username(username):
        """
        Load user and password hash by username from database.

        Primary path: PostgREST. Fallback: direct psycopg2.
        Used during login to authenticate user credentials.

        Args:
            username (str): Username to look up

        Returns:
            tuple: (User instance, password_hash) if found, (None, None) otherwise
        """
        # Primary path: PostgREST
        try:
            response = requests.get(
                f"{POSTGREST_URL}/users",
                params={'username': f'eq.{username}'},
                timeout=3
            )

            if response.status_code == 200:
                users = response.json()
                if users:
                    data = users[0]
                    user = User(
                        id=data['id'],
                        username=data['username'],
                        role=data['role'],
                        must_change_password=data['must_change_password']
                    )
                    return user, data['password_hash']
        except requests.RequestException as e:
            print(f"[User.get_by_username] PostgREST failed for '{username}': {e}, trying direct SQL...")

            # Fallback: direct psycopg2 query
            data = _direct_query(
                "SELECT id, username, role, must_change_password, password_hash FROM users WHERE username = %s",
                (username,), fetch_one=True
            )
            if data:
                user = User(
                    id=data['id'],
                    username=data['username'],
                    role=data['role'],
                    must_change_password=data['must_change_password']
                )
                return user, data['password_hash']

        return None, None

    def __repr__(self):
        """String representation of User object for debugging."""
        return f"<User {self.username} (role={self.role}, id={self.id})>"
