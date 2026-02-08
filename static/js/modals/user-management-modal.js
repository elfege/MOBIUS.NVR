/**
 * User Management Modal
 *
 * Admin-only interface for creating, editing, and deleting users.
 * Provides CRUD operations for user accounts with role-based access control.
 */

class UserManagementModal {
    constructor() {
        this.$modal = $('#user-management-modal');
        this.$backdrop = $('#user-management-backdrop');
        this.$closeBtn = $('#user-management-close');
        this.$userList = $('#user-list');
        this.$addUserBtn = $('#add-user-btn');

        // Form modal
        this.$formModal = $('#user-form-modal');
        this.$formTitle = $('#user-form-title');
        this.$form = $('#user-form');
        this.$formId = $('#user-form-id');
        this.$formUsername = $('#user-form-username');
        this.$formPassword = $('#user-form-password');
        this.$formRole = $('#user-form-role');
        this.$formMustChangePassword = $('#user-form-must-change-password');
        this.$formClose = $('#user-form-close');
        this.$formCancel = $('#user-form-cancel');

        this.init();
    }

    /**
     * Initialize event handlers
     */
    init() {
        // Main modal controls
        this.$closeBtn.on('click', () => this.hide());
        this.$backdrop.on('click', () => this.hide());
        this.$addUserBtn.on('click', () => this.showAddUserForm());

        // Form modal controls
        this.$formClose.on('click', () => this.hideForm());
        this.$formCancel.on('click', () => this.hideForm());
        this.$form.on('submit', (e) => this.handleFormSubmit(e));

        // User list event delegation
        this.$userList.on('click', '.user-edit-btn', (e) => {
            const userId = $(e.currentTarget).data('id');
            this.showEditUserForm(userId);
        });

        this.$userList.on('click', '.user-delete-btn', (e) => {
            const userId = $(e.currentTarget).data('id');
            const username = $(e.currentTarget).data('username');
            this.deleteUser(userId, username);
        });

        this.$userList.on('click', '.user-reset-password-btn', (e) => {
            const userId = $(e.currentTarget).data('id');
            const username = $(e.currentTarget).data('username');
            this.showResetPasswordForm(userId, username);
        });

        // Camera access button
        this.$userList.on('click', '.user-camera-access-btn', (e) => {
            const userId = $(e.currentTarget).data('id');
            const username = $(e.currentTarget).data('username');
            this.showCameraAccessModal(userId, username);
        });
    }

    /**
     * Show user management modal and load users
     */
    async show() {
        await this.loadUsers();
        this.$modal.fadeIn(200);
        this.$backdrop.fadeIn(200);
    }

    /**
     * Hide user management modal
     */
    hide() {
        this.$modal.fadeOut(200);
        this.$backdrop.fadeOut(200);
    }

    /**
     * Load users from API and render list
     */
    async loadUsers() {
        try {
            const response = await fetch('/api/users');

            if (!response.ok) {
                throw new Error('Failed to fetch users');
            }

            const users = await response.json();
            this.renderUserList(users);
        } catch (error) {
            console.error('Error loading users:', error);
            this.$userList.html('<div class="error-message">Failed to load users</div>');
        }
    }

    /**
     * Render user list
     *
     * @param {Array} users - Array of user objects
     */
    renderUserList(users) {
        if (!users || users.length === 0) {
            this.$userList.html('<div class="no-users-message">No users found</div>');
            return;
        }

        const html = users.map(user => `
            <div class="user-item">
                <div class="user-info">
                    <span class="user-username">${this.escapeHtml(user.username)}</span>
                    <span class="user-role role-${user.role}">${user.role}</span>
                </div>
                <div class="user-actions">
                    <button class="btn btn-sm btn-secondary user-edit-btn"
                            data-id="${user.id}"
                            data-username="${this.escapeHtml(user.username)}"
                            data-role="${user.role}">
                        <i class="fas fa-edit"></i>
                    </button>
                    <button class="btn btn-sm btn-secondary user-reset-password-btn"
                            data-id="${user.id}"
                            data-username="${this.escapeHtml(user.username)}"
                            title="Reset Password">
                        <i class="fas fa-key"></i>
                    </button>
                    <button class="btn btn-sm btn-secondary user-camera-access-btn"
                            data-id="${user.id}"
                            data-username="${this.escapeHtml(user.username)}"
                            title="Camera Access">
                        <i class="fas fa-cog"></i>
                    </button>
                    <button class="btn btn-sm btn-danger user-delete-btn"
                            data-id="${user.id}"
                            data-username="${this.escapeHtml(user.username)}">
                        <i class="fas fa-trash"></i>
                    </button>
                </div>
            </div>
        `).join('');

        this.$userList.html(html);
    }

    /**
     * Show add user form
     */
    showAddUserForm() {
        this.$formTitle.html('<i class="fas fa-user-plus"></i> Add User');
        this.$formId.val('');
        this.$formUsername.val('').prop('disabled', false);
        this.$formPassword.val('').prop('required', true);
        this.$formPassword.siblings('small').text('Minimum 8 characters');
        this.$formRole.val('user');
        this.$formMustChangePassword.prop('checked', true); // Default to requiring password change
        this.$formModal.fadeIn(200);
    }

    /**
     * Show edit user form
     *
     * @param {number} userId - User ID
     */
    async showEditUserForm(userId) {
        // Get user data from list
        const $btn = $(`.user-edit-btn[data-id="${userId}"]`);
        const username = $btn.data('username');
        const role = $btn.data('role');

        this.$formTitle.html('<i class="fas fa-edit"></i> Edit User');
        this.$formId.val(userId);
        this.$formUsername.val(username).prop('disabled', true);
        this.$formPassword.val('').prop('required', false);
        this.$formPassword.siblings('small').text('Leave blank to keep existing password');
        this.$formRole.val(role);
        this.$formMustChangePassword.prop('checked', false).closest('.form-group').hide(); // Hide checkbox for edit
        this.$formModal.fadeIn(200);
    }

    /**
     * Hide user form modal
     */
    hideForm() {
        this.$formModal.fadeOut(200);
        this.$form[0].reset();
        this.$formMustChangePassword.closest('.form-group').show(); // Show checkbox again for next use
    }

    /**
     * Handle form submission (add or edit user)
     *
     * @param {Event} e - Submit event
     */
    async handleFormSubmit(e) {
        e.preventDefault();

        const userId = this.$formId.val();
        const username = this.$formUsername.val();
        const password = this.$formPassword.val();
        const role = this.$formRole.val();
        const mustChangePassword = this.$formMustChangePassword.is(':checked');

        const isEdit = !!userId;

        try {
            const data = {
                username,
                role
            };

            // Only include password if provided
            if (password) {
                data.password = password;
            }

            // Include must_change_password when creating new users
            if (!isEdit) {
                data.must_change_password = mustChangePassword;
            }

            const url = isEdit ? `/api/users/${userId}` : '/api/users';
            const method = isEdit ? 'PATCH' : 'POST';

            const response = await fetch(url, {
                method,
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(data)
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Failed to save user');
            }

            // Success - reload users and hide form
            this.hideForm();
            await this.loadUsers();

            // Show success message
            this.showMessage(`User ${isEdit ? 'updated' : 'created'} successfully`, 'success');
        } catch (error) {
            console.error('Error saving user:', error);
            this.showMessage(error.message, 'error');
        }
    }

    /**
     * Delete user
     *
     * @param {number} userId - User ID
     * @param {string} username - Username for confirmation
     */
    async deleteUser(userId, username) {
        if (!confirm(`Are you sure you want to delete user "${username}"?`)) {
            return;
        }

        try {
            const response = await fetch(`/api/users/${userId}`, {
                method: 'DELETE'
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Failed to delete user');
            }

            // Success - reload users
            await this.loadUsers();
            this.showMessage(`User "${username}" deleted`, 'success');
        } catch (error) {
            console.error('Error deleting user:', error);
            this.showMessage(error.message, 'error');
        }
    }

    /**
     * Show reset password form
     *
     * @param {number} userId - User ID
     * @param {string} username - Username for display
     */
    async showResetPasswordForm(userId, username) {
        const newPassword = prompt(`Reset password for "${username}"?\n\nEnter new temporary password (min 8 characters):`);

        if (!newPassword) {
            return; // User cancelled
        }

        if (newPassword.length < 8) {
            this.showMessage('Password must be at least 8 characters', 'error');
            return;
        }

        try {
            const response = await fetch(`/api/users/${userId}/reset-password`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    new_password: newPassword
                })
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Failed to reset password');
            }

            this.showMessage(`Password reset for "${username}". User will be required to change it on next login.`, 'success');
        } catch (error) {
            console.error('Error resetting password:', error);
            this.showMessage(error.message, 'error');
        }
    }

    /**
     * Show camera access modal for a user.
     * Loads all cameras and current access rules, renders checkboxes.
     * Modal has no backdrop exit - only Save/Cancel buttons.
     *
     * @param {number} userId - User ID
     * @param {string} username - Username for display
     */
    async showCameraAccessModal(userId, username) {
        try {
            // Fetch cameras and current access in parallel
            const [camerasRes, accessRes] = await Promise.all([
                fetch('/api/cameras'),
                fetch(`/api/users/${userId}/camera-access`)
            ]);

            if (!camerasRes.ok) throw new Error('Failed to fetch cameras');
            const camerasData = await camerasRes.json();
            const accessData = accessRes.ok ? await accessRes.json() : [];

            // Build set of allowed camera serials
            const allowedSerials = new Set(
                accessData.filter(a => a.allowed).map(a => a.camera_serial)
            );
            const hasRestrictions = accessData.length > 0;

            // Get all cameras from the streaming list
            // /api/cameras returns cameras as a dict keyed by serial number, not an array
            const camerasObj = camerasData.all || {};
            const cameras = Object.entries(camerasObj).map(([serial, cam]) => ({
                serial,
                name: cam.name || cam.display_name || serial,
                ...cam
            }));

            // Build modal HTML
            const cameraCheckboxes = cameras.map(cam => {
                const serial = cam.serial;
                const name = cam.name || serial;
                // If no restrictions exist, all cameras are checked by default
                const checked = hasRestrictions ? allowedSerials.has(serial) : true;
                return `
                    <label class="camera-access-item">
                        <input type="checkbox"
                               class="camera-access-checkbox"
                               data-serial="${this.escapeHtml(serial)}"
                               ${checked ? 'checked' : ''}>
                        <span class="camera-access-name">${this.escapeHtml(name)}</span>
                        <span class="camera-access-serial">${this.escapeHtml(serial)}</span>
                    </label>
                `;
            }).join('');

            // Remove existing modal if present
            $('#camera-access-modal').remove();

            // Create fixed modal (no backdrop exit)
            const modalHtml = `
                <div id="camera-access-modal" class="camera-access-modal">
                    <div class="camera-access-panel">
                        <div class="modal-header">
                            <h3><i class="fas fa-video"></i> Camera Access: ${this.escapeHtml(username)}</h3>
                        </div>
                        <div class="camera-access-body">
                            <div class="camera-access-controls">
                                <button type="button" class="btn btn-sm btn-secondary" id="camera-access-select-all">Select All</button>
                                <button type="button" class="btn btn-sm btn-secondary" id="camera-access-select-none">Select None</button>
                            </div>
                            <div class="camera-access-list">
                                ${cameraCheckboxes || '<div class="no-cameras">No cameras configured</div>'}
                            </div>
                        </div>
                        <div class="camera-access-footer">
                            <button type="button" class="btn btn-secondary" id="camera-access-cancel">Cancel</button>
                            <button type="button" class="btn btn-primary" id="camera-access-save">Save</button>
                        </div>
                    </div>
                </div>
            `;

            $('body').append(modalHtml);

            const $modal = $('#camera-access-modal');

            // Select All / Select None
            $('#camera-access-select-all').on('click', () => {
                $modal.find('.camera-access-checkbox').prop('checked', true);
            });
            $('#camera-access-select-none').on('click', () => {
                $modal.find('.camera-access-checkbox').prop('checked', false);
            });

            // Cancel - just close
            $('#camera-access-cancel').on('click', () => {
                $modal.remove();
            });

            // Save - collect checked cameras and PUT to API
            $('#camera-access-save').on('click', async () => {
                const cameraAccess = [];
                $modal.find('.camera-access-checkbox').each(function() {
                    cameraAccess.push({
                        camera_serial: $(this).data('serial'),
                        allowed: $(this).is(':checked')
                    });
                });

                // Check if all cameras are selected
                const allChecked = cameraAccess.every(c => c.allowed);

                try {
                    const response = await fetch(`/api/users/${userId}/camera-access`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            // If all checked, send empty list (means "all access")
                            cameras: allChecked ? [] : cameraAccess
                        })
                    });

                    if (!response.ok) {
                        const err = await response.json();
                        throw new Error(err.error || 'Failed to save');
                    }

                    $modal.remove();
                    this.showMessage(`Camera access updated for "${username}"`, 'success');
                } catch (error) {
                    console.error('Error saving camera access:', error);
                    this.showMessage(error.message, 'error');
                }
            });

            // Show modal with fade
            $modal.fadeIn(200);

        } catch (error) {
            console.error('Error loading camera access:', error);
            this.showMessage(error.message, 'error');
        }
    }

    /**
     * Show temporary message
     *
     * @param {string} message - Message text
     * @param {string} type - Message type ('success' or 'error')
     */
    showMessage(message, type = 'success') {
        const $message = $(`
            <div class="user-management-message ${type}">
                ${this.escapeHtml(message)}
            </div>
        `);

        this.$modal.find('.modal-header').after($message);

        setTimeout(() => {
            $message.fadeOut(300, function() {
                $(this).remove();
            });
        }, 3000);
    }

    /**
     * Escape HTML to prevent XSS
     *
     * @param {string} str - String to escape
     * @returns {string} Escaped string
     */
    escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }
}

// Initialize user management modal on page load
$(document).ready(function() {
    const userManagementModal = new UserManagementModal();

    // Attach to global window object for access from other scripts
    window.userManagementModal = userManagementModal;

    // Handle Manage Users button click (both old header button and new nav menu button)
    $('#manage-users-btn, #menu-manage-users').on('click', function() {
        /* Close the nav menu if open */
        $('#nav-menu').removeClass('open');
        $('#nav-menu-overlay').removeClass('show');
        userManagementModal.show();
    });
});
