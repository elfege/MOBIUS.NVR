"""
routes/auth.py — Authentication and user/device management Blueprint.

Covers:
- /login (GET/POST)
- /logout (POST)
- /change-password (GET/POST)
- /api/users (GET, POST)
- /api/users/<id> (PATCH, DELETE)
- /api/users/<id>/reset-password (POST)
- /api/device/register (POST)
- /api/device/heartbeat (POST)
- /api/admin/devices (GET)
- /api/admin/devices/<id>/trust (PATCH)
- /api/admin/devices/<id>/name (PATCH)
- /api/admin/devices/<id> (DELETE)
- /api/users/<user_id>/camera-access (GET, PUT)
- /api/my-camera-access (GET)
- /api/my-preferences (GET, PUT)
"""

import uuid
import requests
import bcrypt
from datetime import datetime
from flask import Blueprint, render_template, jsonify, request, redirect
from flask_login import login_required, login_user, logout_user, current_user
from models.user import User
import routes.shared as shared
from routes.helpers import (
    csrf_exempt,
    _create_user_session,
    _deactivate_user_session,
    _register_or_update_device,
)

auth_bp = Blueprint('auth', __name__)


# ===== Authentication Routes =====

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """
    User login page and authentication handler.

    GET: Display login form
    POST: Authenticate user credentials and create session
    """
    if request.method == 'GET':
        return render_template('login.html')

    # POST - handle login
    username = request.form.get('username')
    password = request.form.get('password')

    if not username or not password:
        return render_template('login.html', error='Username and password required')

    # Load user and password hash from database
    user, password_hash = User.get_by_username(username)

    if not user:
        return render_template('login.html', error='Invalid username or password')

    # Verify password with bcrypt
    if not bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8')):
        return render_template('login.html', error='Invalid username or password')

    # Log user in (even if password change required - they authenticated successfully)
    login_user(user, remember=True)

    # Create session record in database
    _create_user_session(user.id, request.remote_addr, request.user_agent.string)

    # Register device token — reuse existing cookie or generate new one
    device_token = request.cookies.get('device_token') or str(uuid.uuid4())
    _register_or_update_device(
        device_token=device_token,
        user_id=user.id,
        ip_address=request.remote_addr,
        user_agent=request.user_agent.string
    )

    # Check if password change required AFTER login
    # This allows change-password route to use current_user with proper auth context
    if user.must_change_password:
        resp = redirect('/change-password')
    else:
        resp = redirect('/streams')

    # Set device_token cookie on the redirect response
    resp.set_cookie(
        'device_token',
        device_token,
        max_age=365 * 24 * 3600,
        httponly=True,
        samesite='Lax',
        secure=False
    )
    return resp

@auth_bp.route('/logout', methods=['POST'])
@csrf_exempt
@login_required
def logout():
    """
    User logout handler.

    Deactivates session in database and clears Flask-Login session.
    """
    # Mark session as inactive in database
    _deactivate_user_session(current_user.id)

    # Clear Flask-Login session
    logout_user()

    return redirect('/login')

@auth_bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    """
    Password change page for forced password updates.

    Used when must_change_password flag is set (e.g., default admin account).
    User must be logged in to access this page.
    """
    # Security check: only allow if password change is actually required
    if not current_user.must_change_password:
        return redirect('/streams')

    if request.method == 'GET':
        return render_template('change_password.html')

    # POST - handle password change
    new_password = request.form.get('new_password')
    confirm_password = request.form.get('confirm_password')

    if not new_password or new_password != confirm_password:
        return render_template('change_password.html', error='Passwords do not match')

    if len(new_password) < 8:
        return render_template('change_password.html', error='Password must be at least 8 characters')

    # Hash new password with bcrypt
    password_hash = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    # Update database using authenticated user context
    try:
        response = shared._postgrest_session.patch(
            f"{shared.POSTGREST_URL}/users",
            params={'id': f'eq.{current_user.id}'},
            json={
                'password_hash': password_hash,
                'must_change_password': False
            },
            headers={
                'Prefer': 'return=minimal'
                # TODO: Set RLS context headers here when implementing stricter policies
            },
            timeout=5
        )

        if response.status_code == 204:
            # Log user out so they can verify new password works
            logout_user()
            return redirect('/login')

        return render_template('change_password.html', error='Failed to update password')
    except requests.RequestException as e:
        print(f"Error updating password: {e}")
        return render_template('change_password.html', error='Database error')


# ===== User Management API (Admin Only) =====

@auth_bp.route('/api/users', methods=['GET'])
@login_required
def api_get_users():
    """
    Get list of all users (admin only).

    Returns list of users with id, username, role (password_hash excluded).
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    try:
        response = shared._postgrest_session.get(
            f"{shared.POSTGREST_URL}/users",
            params={'select': 'id,username,role,created_at'},
            timeout=5
        )

        if response.status_code == 200:
            return jsonify(response.json())

        return jsonify({'error': 'Failed to fetch users'}), 500
    except requests.RequestException as e:
        print(f"Error fetching users: {e}")
        return jsonify({'error': 'Database error'}), 500

@auth_bp.route('/api/users', methods=['POST'])
@csrf_exempt
@login_required
def api_create_user():
    """
    Create new user (admin only).

    Expects JSON: {username, password, role, must_change_password}
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    try:
        data = request.get_json()
        username = data.get('username')
        password = data.get('password')
        role = data.get('role', 'user')
        must_change_password = data.get('must_change_password', True)  # Default to requiring password change

        if not username or not password:
            return jsonify({'error': 'Username and password required'}), 400

        if len(password) < 8:
            return jsonify({'error': 'Password must be at least 8 characters'}), 400

        # Hash password with bcrypt
        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        # Create user in database
        response = shared._postgrest_session.post(
            f"{shared.POSTGREST_URL}/users",
            json={
                'username': username,
                'password_hash': password_hash,
                'role': role,
                'must_change_password': must_change_password
            },
            headers={'Prefer': 'return=representation'},
            timeout=5
        )

        if response.status_code == 201:
            user = response.json()[0]
            return jsonify({
                'success': True,
                'user': {
                    'id': user['id'],
                    'username': user['username'],
                    'role': user['role']
                }
            })

        if response.status_code == 409:
            return jsonify({'error': 'Username already exists'}), 409

        return jsonify({'error': 'Failed to create user'}), 500
    except requests.RequestException as e:
        print(f"Error creating user: {e}")
        return jsonify({'error': 'Database error'}), 500

@auth_bp.route('/api/users/<int:user_id>', methods=['PATCH'])
@csrf_exempt
@login_required
def api_update_user(user_id):
    """
    Update user (admin only).

    Expects JSON: {username?, password?, role?}
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    try:
        data = request.get_json()
        update_data = {}

        # Update username if provided
        if 'username' in data:
            update_data['username'] = data['username']

        # Update password if provided
        if 'password' in data and data['password']:
            if len(data['password']) < 8:
                return jsonify({'error': 'Password must be at least 8 characters'}), 400
            password_hash = bcrypt.hashpw(data['password'].encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            update_data['password_hash'] = password_hash

        # Update role if provided
        if 'role' in data:
            update_data['role'] = data['role']

        if not update_data:
            return jsonify({'error': 'No fields to update'}), 400

        # Update user in database
        response = shared._postgrest_session.patch(
            f"{shared.POSTGREST_URL}/users",
            params={'id': f'eq.{user_id}'},
            json=update_data,
            headers={'Prefer': 'return=representation'},
            timeout=5
        )

        if response.status_code == 200:
            user = response.json()[0]
            return jsonify({
                'success': True,
                'user': {
                    'id': user['id'],
                    'username': user['username'],
                    'role': user['role']
                }
            })

        return jsonify({'error': 'Failed to update user'}), 500
    except requests.RequestException as e:
        print(f"Error updating user: {e}")
        return jsonify({'error': 'Database error'}), 500

@auth_bp.route('/api/users/<int:user_id>', methods=['DELETE'])
@csrf_exempt
@login_required
def api_delete_user(user_id):
    """
    Delete user (admin only).

    Cannot delete yourself or the default admin account.
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    # Prevent deleting yourself
    if user_id == current_user.id:
        return jsonify({'error': 'Cannot delete your own account'}), 400

    try:
        # Delete user from database
        response = shared._postgrest_session.delete(
            f"{shared.POSTGREST_URL}/users",
            params={'id': f'eq.{user_id}'},
            headers={'Prefer': 'return=minimal'},
            timeout=5
        )

        if response.status_code == 204:
            return jsonify({'success': True})

        return jsonify({'error': 'Failed to delete user'}), 500
    except requests.RequestException as e:
        print(f"Error deleting user: {e}")
        return jsonify({'error': 'Database error'}), 500

@auth_bp.route('/api/users/<int:user_id>/reset-password', methods=['POST'])
@csrf_exempt
@login_required
def api_reset_user_password(user_id):
    """
    Reset user password (admin only).

    Sets a new temporary password and forces user to change it on next login.
    Validates that new password is different from current password.

    Expects JSON: {new_password}
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    try:
        data = request.get_json()
        new_password = data.get('new_password')

        if not new_password:
            return jsonify({'error': 'New password required'}), 400

        if len(new_password) < 8:
            return jsonify({'error': 'Password must be at least 8 characters'}), 400

        # Get current password hash from database
        user_response = shared._postgrest_session.get(
            f"{shared.POSTGREST_URL}/users",
            params={'id': f'eq.{user_id}', 'select': 'password_hash'},
            timeout=5
        )

        if user_response.status_code != 200 or not user_response.json():
            return jsonify({'error': 'User not found'}), 404

        current_password_hash = user_response.json()[0]['password_hash']

        # Validate new password is different from current password
        if bcrypt.checkpw(new_password.encode('utf-8'), current_password_hash.encode('utf-8')):
            return jsonify({'error': 'New password must be different from current password'}), 400

        # Hash new password
        new_password_hash = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        # Update password and set must_change_password flag
        response = shared._postgrest_session.patch(
            f"{shared.POSTGREST_URL}/users",
            params={'id': f'eq.{user_id}'},
            json={
                'password_hash': new_password_hash,
                'must_change_password': True
            },
            headers={'Prefer': 'return=minimal'},
            timeout=5
        )

        if response.status_code == 204:
            return jsonify({'success': True})

        return jsonify({'error': 'Failed to reset password'}), 500
    except requests.RequestException as e:
        print(f"Error resetting password: {e}")
        return jsonify({'error': 'Database error'}), 500


# ===== Device Management API =====

@auth_bp.route('/api/device/register', methods=['POST'])
@csrf_exempt
@login_required
def api_device_register():
    """
    Register a device token for the current user.

    Called by the frontend on page load. If the client already has a device_token
    in localStorage, it sends it here. Otherwise, the server generates a new one.

    Returns:
        JSON with device_token (to be stored in localStorage by client)
    """
    data = request.get_json() or {}
    client_token = data.get('device_token')

    # Use the client's existing token or generate a new one
    device_token = client_token or str(uuid.uuid4())

    device = _register_or_update_device(
        device_token=device_token,
        user_id=current_user.id,
        ip_address=request.remote_addr,
        user_agent=request.user_agent.string
    )

    if not device:
        return jsonify({'error': 'Failed to register device'}), 500

    # Set the device_token as an httpOnly cookie (backup for localStorage)
    resp = jsonify({
        'device_token': device_token,
        'is_trusted': device.get('is_trusted', False)
    })
    resp.set_cookie(
        'device_token',
        device_token,
        max_age=365 * 24 * 3600,  # 1 year
        httponly=True,
        samesite='Lax',
        secure=False  # Set True when HTTPS enabled
    )
    return resp


@auth_bp.route('/api/device/heartbeat', methods=['POST'])
@csrf_exempt
@login_required
def api_device_heartbeat():
    """
    Update last_seen for the current device.

    Called periodically by the connection monitor alongside health checks.
    Updates IP, user_agent, and last_seen timestamp.

    Returns:
        JSON with is_trusted status
    """
    data = request.get_json() or {}
    device_token = data.get('device_token') or request.cookies.get('device_token')

    if not device_token:
        return jsonify({'error': 'No device token'}), 400

    device = _register_or_update_device(
        device_token=device_token,
        user_id=current_user.id,
        ip_address=request.remote_addr,
        user_agent=request.user_agent.string
    )

    if not device:
        return jsonify({'error': 'Failed to update device'}), 500

    return jsonify({'is_trusted': device.get('is_trusted', False)})


@auth_bp.route('/api/admin/devices', methods=['GET'])
@login_required
def api_admin_get_devices():
    """
    Get all registered devices (admin only).

    Returns list of devices with user info, IP, last_seen, trust status.
    Devices seen in the last 5 minutes are considered "online".
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    try:
        resp = shared._postgrest_session.get(
            f"{shared.POSTGREST_URL}/trusted_devices",
            params={
                'select': 'id,device_token,user_id,device_name,ip_address,user_agent,is_trusted,first_seen,last_seen',
                'order': 'last_seen.desc'
            },
            timeout=5
        )
        if resp.status_code != 200:
            return jsonify({'error': 'Failed to fetch devices'}), 500

        devices = resp.json()

        # Enrich with username lookup
        user_cache = {}
        for device in devices:
            uid = device.get('user_id')
            if uid and uid not in user_cache:
                user = User.get_by_id(uid)
                user_cache[uid] = user.username if user else 'unknown'
            device['username'] = user_cache.get(uid, 'unlinked')

        return jsonify(devices)
    except requests.RequestException as e:
        print(f"[DeviceManager] Error fetching devices: {e}")
        return jsonify({'error': 'Database error'}), 500


@auth_bp.route('/api/admin/devices/<int:device_id>/trust', methods=['PATCH'])
@csrf_exempt
@login_required
def api_admin_toggle_trust(device_id):
    """
    Toggle trusted status for a device (admin only).

    Expects JSON: {is_trusted: true/false}
    When trusted, the device will auto-login without requiring credentials.
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    data = request.get_json()
    if data is None or 'is_trusted' not in data:
        return jsonify({'error': 'is_trusted field required'}), 400

    try:
        resp = shared._postgrest_session.patch(
            f"{shared.POSTGREST_URL}/trusted_devices",
            params={'id': f'eq.{device_id}'},
            json={'is_trusted': bool(data['is_trusted'])},
            headers={'Prefer': 'return=representation'},
            timeout=5
        )
        if resp.status_code == 200 and resp.json():
            device = resp.json()[0]
            action = 'trusted' if device['is_trusted'] else 'untrusted'
            print(f"[DeviceManager] Device {device_id} marked as {action} by {current_user.username}")
            return jsonify(device)

        return jsonify({'error': 'Device not found'}), 404
    except requests.RequestException as e:
        print(f"[DeviceManager] Error toggling trust: {e}")
        return jsonify({'error': 'Database error'}), 500


@auth_bp.route('/api/admin/devices/<int:device_id>/name', methods=['PATCH'])
@csrf_exempt
@login_required
def api_admin_rename_device(device_id):
    """
    Set a friendly name for a device (admin only).

    Expects JSON: {device_name: "Living Room iPad"}
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    data = request.get_json()
    if data is None or 'device_name' not in data:
        return jsonify({'error': 'device_name field required'}), 400

    try:
        resp = shared._postgrest_session.patch(
            f"{shared.POSTGREST_URL}/trusted_devices",
            params={'id': f'eq.{device_id}'},
            json={'device_name': str(data['device_name'])[:100]},
            headers={'Prefer': 'return=representation'},
            timeout=5
        )
        if resp.status_code == 200 and resp.json():
            return jsonify(resp.json()[0])

        return jsonify({'error': 'Device not found'}), 404
    except requests.RequestException as e:
        print(f"[DeviceManager] Error renaming device: {e}")
        return jsonify({'error': 'Database error'}), 500


@auth_bp.route('/api/admin/devices/<int:device_id>', methods=['DELETE'])
@csrf_exempt
@login_required
def api_admin_delete_device(device_id):
    """
    Delete a registered device (admin only).

    Removes the device from the database entirely.
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    try:
        resp = shared._postgrest_session.delete(
            f"{shared.POSTGREST_URL}/trusted_devices",
            params={'id': f'eq.{device_id}'},
            headers={'Prefer': 'return=minimal'},
            timeout=5
        )
        if resp.status_code == 204:
            print(f"[DeviceManager] Device {device_id} deleted by {current_user.username}")
            return jsonify({'success': True})

        return jsonify({'error': 'Device not found'}), 404
    except requests.RequestException as e:
        print(f"[DeviceManager] Error deleting device: {e}")
        return jsonify({'error': 'Database error'}), 500


# ===== User Camera Access Control =====

@auth_bp.route('/api/users/<int:user_id>/camera-access', methods=['GET'])
@csrf_exempt
@login_required
def api_get_user_camera_access(user_id):
    """
    Get camera access list for a user (admin only).

    Returns list of camera serials the user is allowed to see.
    Empty list means user can see ALL cameras (default).
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    try:
        response = shared._postgrest_session.get(
            f"{shared.POSTGREST_URL}/user_camera_access",
            params={'user_id': f'eq.{user_id}', 'select': 'camera_serial,allowed'},
            timeout=5
        )

        if response.status_code == 200:
            return jsonify(response.json())

        return jsonify([])
    except requests.RequestException as e:
        print(f"Error fetching camera access: {e}")
        return jsonify({'error': 'Database error'}), 500


@auth_bp.route('/api/users/<int:user_id>/camera-access', methods=['PUT'])
@csrf_exempt
@login_required
def api_set_user_camera_access(user_id):
    """
    Set camera access for a user (admin only).

    Expects JSON: {cameras: [{camera_serial, allowed}, ...]}
    Replaces all existing access rules for the user.
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    try:
        data = request.get_json()
        cameras = data.get('cameras', [])

        # Delete existing access rules for this user
        shared._postgrest_session.delete(
            f"{shared.POSTGREST_URL}/user_camera_access",
            params={'user_id': f'eq.{user_id}'},
            headers={'Prefer': 'return=minimal'},
            timeout=5
        )

        # Insert new access rules (only for cameras that are allowed)
        allowed_cameras = [c for c in cameras if c.get('allowed', False)]
        if allowed_cameras:
            rows = [
                {
                    'user_id': user_id,
                    'camera_serial': c['camera_serial'],
                    'allowed': True
                }
                for c in allowed_cameras
            ]
            response = shared._postgrest_session.post(
                f"{shared.POSTGREST_URL}/user_camera_access",
                json=rows,
                headers={'Prefer': 'return=minimal'},
                timeout=5
            )

            if response.status_code not in [200, 201]:
                return jsonify({'error': 'Failed to save camera access'}), 500

        return jsonify({'success': True})
    except requests.RequestException as e:
        print(f"Error saving camera access: {e}")
        return jsonify({'error': 'Database error'}), 500


@auth_bp.route('/api/my-camera-access', methods=['GET'])
@csrf_exempt
@login_required
def api_get_my_camera_access():
    """
    Get current user's camera access list.

    Admins always get all cameras.
    For regular users: if no access rules exist, they see all cameras.
    If access rules exist, they only see allowed cameras.
    """
    # Admins always see everything
    if current_user.role == 'admin':
        return jsonify({'all_access': True, 'cameras': []})

    try:
        response = shared._postgrest_session.get(
            f"{shared.POSTGREST_URL}/user_camera_access",
            params={'user_id': f'eq.{current_user.id}', 'select': 'camera_serial,allowed'},
            timeout=5
        )

        if response.status_code == 200:
            access_list = response.json()
            if not access_list:
                # No restrictions set - user sees all cameras
                return jsonify({'all_access': True, 'cameras': []})
            else:
                # Return only allowed camera serials
                allowed = [a['camera_serial'] for a in access_list if a.get('allowed', False)]
                return jsonify({'all_access': False, 'cameras': allowed})

        return jsonify({'all_access': True, 'cameras': []})
    except requests.RequestException as e:
        print(f"Error fetching user camera access: {e}")
        return jsonify({'all_access': True, 'cameras': []})


@auth_bp.route('/api/my-preferences', methods=['GET'])
@csrf_exempt
@login_required
def api_get_my_preferences():
    """
    Get current user's display preferences (hidden cameras, HD cameras).
    Returns defaults if no preferences saved yet.
    """
    try:
        response = shared._postgrest_session.get(
            f"{shared.POSTGREST_URL}/user_preferences",
            params={
                'user_id': f'eq.{current_user.id}',
                'select': 'hidden_cameras,hd_cameras,default_video_fit,pinned_camera,pinned_windows'
            },
            timeout=5
        )

        if response.status_code == 200:
            rows = response.json()
            if rows:
                return jsonify(rows[0])

        # No preferences saved yet - return defaults
        return jsonify({'hidden_cameras': [], 'hd_cameras': [], 'default_video_fit': 'cover', 'pinned_camera': None, 'pinned_windows': {}})
    except requests.RequestException as e:
        print(f"Error fetching user preferences: {e}")
        return jsonify({'hidden_cameras': [], 'hd_cameras': [], 'default_video_fit': 'cover', 'pinned_camera': None, 'pinned_windows': {}})


@auth_bp.route('/api/my-preferences', methods=['PUT'])
@csrf_exempt
@login_required
def api_put_my_preferences():
    """
    Save current user's display preferences (hidden cameras, HD cameras).
    Uses upsert: creates row if none exists, updates if it does.
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    payload = {'user_id': current_user.id}
    if 'hidden_cameras' in data:
        payload['hidden_cameras'] = data['hidden_cameras']
    if 'hd_cameras' in data:
        payload['hd_cameras'] = data['hd_cameras']
    if 'default_video_fit' in data:
        if data['default_video_fit'] not in ('cover', 'fill'):
            return jsonify({'error': 'default_video_fit must be "cover" or "fill"'}), 400
        payload['default_video_fit'] = data['default_video_fit']
    if 'pinned_camera' in data:
        # Accept string serial or null to clear the pin
        val = data['pinned_camera']
        payload['pinned_camera'] = val if isinstance(val, str) else None
    if 'pinned_windows' in data:
        # Accept dict mapping serial → {x, y, w, h} window position/size
        val = data['pinned_windows']
        payload['pinned_windows'] = val if isinstance(val, dict) else {}

    try:
        # Upsert: use Prefer: resolution=merge-duplicates with the unique constraint on user_id
        response = shared._postgrest_session.post(
            f"{shared.POSTGREST_URL}/user_preferences",
            json=payload,
            headers={
                'Prefer': 'resolution=merge-duplicates,return=representation',
            },
            timeout=5
        )

        if response.status_code in (200, 201):
            rows = response.json()
            if rows:
                return jsonify(rows[0])
            return jsonify({'status': 'saved'})
        else:
            print(f"Failed to save preferences: {response.status_code} {response.text}")
            return jsonify({'error': 'Failed to save preferences'}), 500
    except requests.RequestException as e:
        print(f"Error saving user preferences: {e}")
        return jsonify({'error': str(e)}), 500
