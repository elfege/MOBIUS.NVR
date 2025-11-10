/**
 * Eufy Authentication Notifier
 * Polls Flask API and shows notification when auth is required
 */
class EufyAuthNotifier {
    constructor() {
        this.pollInterval = 10000; // Check every 10 seconds
        this.notificationTimeout = 30000; // Auto-hide after 30 seconds
        this.notification = null;
        this.timeoutId = null;
        this.intervalId = null;
        this.isAuthRequired = false;
    }

    start() {
        console.log('[Eufy Auth] Starting notification monitor');
        this.checkAuthStatus();
        this.intervalId = setInterval(() => this.checkAuthStatus(), this.pollInterval);
    }

    stop() {
        if (this.intervalId) {
            clearInterval(this.intervalId);
            this.intervalId = null;
        }
        if (this.timeoutId) {
            clearTimeout(this.timeoutId);
            this.timeoutId = null;
        }
    }

    async checkAuthStatus() {
        try {
            const response = await fetch('/api/eufy-auth/status');
            const data = await response.json();
            
            if (!data.connected && !this.notification && !this.isAuthRequired) {
                console.log('[Eufy Auth] Authentication required - showing notification');
                this.showNotification();
                this.isAuthRequired = true;
            }
            
            if (data.connected && this.notification) {
                console.log('[Eufy Auth] Authentication successful - hiding notification');
                this.hideNotification();
                this.isAuthRequired = false;
            }
        } catch (error) {
            console.error('[Eufy Auth] Error checking status:', error);
        }
    }

    showNotification() {
        if (this.notification) return;

        const protocol = window.location.protocol;
        const hostname = window.location.hostname;
        const port = window.location.port || (protocol === 'https:' ? '443' : '80');
        const authUrl = `${protocol}//${hostname}:${port}/eufy-auth`;

        this.notification = document.createElement('div');
        this.notification.className = 'eufy-auth-notification';
        this.notification.innerHTML = `
            <button class="eufy-auth-notification-close" onclick="eufyAuthNotifier.hideNotification()">
                <i class="fas fa-times"></i>
            </button>
            <div class="eufy-auth-notification-header">
                <i class="fas fa-shield-alt"></i>
                Eufy Authentication Required
            </div>
            <div class="eufy-auth-notification-body">
                Your Eufy bridge needs authentication to connect. Please complete the 2-step verification process.
            </div>
            <a href="${authUrl}" class="eufy-auth-notification-link" target="_blank">
                <i class="fas fa-external-link-alt"></i> Authenticate Now
            </a>
        `;

        document.body.appendChild(this.notification);

        this.timeoutId = setTimeout(() => {
            this.hideNotification();
        }, this.notificationTimeout);
    }

    hideNotification() {
        if (!this.notification) return;

        this.notification.classList.add('fade-out');

        setTimeout(() => {
            if (this.notification && this.notification.parentNode) {
                this.notification.parentNode.removeChild(this.notification);
            }
            this.notification = null;
        }, 1000);

        if (this.timeoutId) {
            clearTimeout(this.timeoutId);
            this.timeoutId = null;
        }
    }
}

const eufyAuthNotifier = new EufyAuthNotifier();

document.addEventListener('DOMContentLoaded', function() {
    console.log('[Eufy Auth] Initializing notification system');
    eufyAuthNotifier.start();
});

window.addEventListener('beforeunload', function() {
    eufyAuthNotifier.stop();
});