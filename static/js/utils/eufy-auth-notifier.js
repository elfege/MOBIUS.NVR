/**
 * Eufy Authentication Notifier
 * Polls Flask API and shows notification when auth is required
 */

let eufyAuthNotifier;

class EufyAuthNotifier {
    constructor() {
        this.pollInterval = 10000; // Check every 10 seconds
        this.notificationTimeout = 2000; // Auto-hide after N seconds
        this.$notification = null;
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
            const response = await axios.get('/api/eufy-auth/status');
            const data = response.data;

            if (!data.connected && !this.$notification && !this.isAuthRequired) {
                console.log('[Eufy Auth] Authentication required - showing notification');
                this.showNotification();
                this.isAuthRequired = true;
            }

            if (data.connected && this.$notification) {
                console.log('[Eufy Auth] Authentication successful - hiding notification');
                this.hideNotification();
                this.isAuthRequired = false;
            }
        } catch (error) {
            console.error('[Eufy Auth] Error checking status:', error);
        }
    }

    showNotification() {
        if (this.$notification) return;

        const protocol = window.location.protocol;
        const hostname = window.location.hostname;
        const port = window.location.port || (protocol === 'https:' ? '443' : '80');
        const authUrl = `${protocol}//${hostname}:${port}/eufy-auth`;

        this.$notification = $(`
            <div class="eufy-auth-notification">
                <button class="eufy-auth-notification-close">
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
            </div>
        `);

        this.$notification.find('.eufy-auth-notification-close').on('click', () => {
            this.hideNotification();
        });

        $('body').append(this.$notification);

        this.timeoutId = setTimeout(() => {
            this.hideNotification();
        }, this.notificationTimeout);
    }

    hideNotification() {
        if (!this.$notification) return;

        this.$notification.addClass('fade-out');

        setTimeout(() => {
            if (this.$notification) {
                this.$notification.remove();
                this.$notification = null;
            }
        }, 1000);

        if (this.timeoutId) {
            clearTimeout(this.timeoutId);
            this.timeoutId = null;
        }
    }
}

eufyAuthNotifier = new EufyAuthNotifier();

$(() => {
    console.log('[Eufy Auth] Initializing notification system');
    // eufyAuthNotifier.start(); # Disabled by default since Eufy Bridge is not in use for now
});

$(window).on('beforeunload', () => {
    // eufyAuthNotifier.stop(); # Disabled by default since Eufy Bridge is not in use for now
});