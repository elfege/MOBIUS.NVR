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
        this.$formModal.fadeIn(200);
    }

    /**
     * Hide user form modal
     */
    hideForm() {
        this.$formModal.fadeOut(200);
        this.$form[0].reset();
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

    // Handle Manage Users button click
    $('#manage-users-btn').on('click', function() {
        userManagementModal.show();
    });
});
