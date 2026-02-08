"""
User Model for Flask-Login Authentication

This module provides the User model that integrates Flask-Login with PostgREST.
Users are stored in the PostgreSQL database and accessed via PostgREST REST API.

Classes:
    User: Flask-Login user model with PostgREST integration
"""

from flask_login import UserMixin
import requests
import os

# PostgREST connection URL (containerized service)
POSTGREST_URL = os.getenv('POSTGREST_URL', 'http://postgrest:3001')


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
        Load user by ID from database via PostgREST.

        Used by Flask-Login's @login_manager.user_loader decorator to reload
        the user object from the user ID stored in the session.

        Args:
            user_id (int): User ID to look up

        Returns:
            User: User instance if found, None otherwise
        """
        try:
            response = requests.get(
                f"{POSTGREST_URL}/users",
                params={'id': f'eq.{user_id}'},
                timeout=5
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
            print(f"Error loading user by ID {user_id}: {e}")

        return None

    @staticmethod
    def get_by_username(username):
        """
        Load user and password hash by username from database.

        Used during login to authenticate user credentials.
        Returns both the User object and the password hash for bcrypt verification.

        Args:
            username (str): Username to look up

        Returns:
            tuple: (User instance, password_hash) if found, (None, None) otherwise
        """
        try:
            response = requests.get(
                f"{POSTGREST_URL}/users",
                params={'username': f'eq.{username}'},
                timeout=5
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
            print(f"Error loading user by username {username}: {e}")

        return None, None

    def __repr__(self):
        """String representation of User object for debugging."""
        return f"<User {self.username} (role={self.role}, id={self.id})>"
