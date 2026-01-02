/**
 * Connection Monitor Module
 * Detects when the NVR server becomes unresponsive and redirects to reloading page
 */

export class ConnectionMonitor {
    constructor() {
        this.checkInterval = null;
        this.failedChecks = 0;
        this.maxFailedChecks = 3; // Fail after 3 consecutive failures
        this.checkIntervalMs = 10000; // Check every 10 seconds
        this.isMonitoring = false;

        console.log('[ConnectionMonitor] Initialized');
    }

    /**
     * Start monitoring server connection
     */
    start() {
        if (this.isMonitoring) {
            console.log('[ConnectionMonitor] Already monitoring');
            return;
        }

        this.isMonitoring = true;
        this.failedChecks = 0;

        console.log(`[ConnectionMonitor] Starting health checks every ${this.checkIntervalMs / 1000}s`);

        // Perform checks at regular intervals
        this.checkInterval = setInterval(() => {
            this.performHealthCheck();
        }, this.checkIntervalMs);

        // Also listen for fetch errors globally
        this.setupFetchInterceptor();
    }

    /**
     * Stop monitoring
     */
    stop() {
        if (this.checkInterval) {
            clearInterval(this.checkInterval);
            this.checkInterval = null;
        }
        this.isMonitoring = false;
        this.failedChecks = 0;
        console.log('[ConnectionMonitor] Stopped');
    }

    /**
     * Perform a health check against the server
     */
    async performHealthCheck() {
        try {
            const response = await fetch('/api/health', {
                method: 'GET',
                cache: 'no-store',
                signal: AbortSignal.timeout(5000) // 5 second timeout
            });

            if (response.ok) {
                // Server is healthy, reset failure count
                if (this.failedChecks > 0) {
                    console.log('[ConnectionMonitor] Server recovered');
                }
                this.failedChecks = 0;
            } else if (response.status === 503) {
                // Server is shutting down - immediate redirect
                const data = await response.json();
                if (data.status === 'shutting_down') {
                    console.warn('[ConnectionMonitor] Server is shutting down, redirecting immediately');
                    this.redirectToReloadingPage();
                    return;
                }
            } else {
                this.handleFailedCheck(`HTTP ${response.status}`);
            }
        } catch (error) {
            this.handleFailedCheck(error.message);
        }
    }

    /**
     * Handle a failed health check
     */
    handleFailedCheck(reason) {
        this.failedChecks++;
        console.warn(`[ConnectionMonitor] Health check failed (${this.failedChecks}/${this.maxFailedChecks}): ${reason}`);

        if (this.failedChecks >= this.maxFailedChecks) {
            console.error('[ConnectionMonitor] Server appears to be down, redirecting to reloading page');
            this.redirectToReloadingPage();
        }
    }

    /**
     * Redirect to the reloading page
     */
    redirectToReloadingPage() {
        // Stop monitoring to prevent multiple redirects
        this.stop();

        // Store current URL so we can return to it after reconnection
        localStorage.setItem('nvr_return_url', window.location.href);
        localStorage.setItem('nvr_reconnect_attempt', '1');

        // Redirect to reloading page
        window.location.href = '/nginx/reloading.html';
    }

    /**
     * Setup global fetch interceptor to detect network errors
     * This catches errors that happen during API calls from other parts of the app
     */
    setupFetchInterceptor() {
        // Track consecutive fetch errors
        let consecutiveErrors = 0;
        const errorThreshold = 5; // Redirect after 5 consecutive fetch errors

        // Store original fetch
        const originalFetch = window.fetch;

        // Override fetch
        window.fetch = async (...args) => {
            try {
                const response = await originalFetch(...args);

                // Reset error counter on successful response
                if (response.ok) {
                    consecutiveErrors = 0;
                }

                return response;
            } catch (error) {
                // Check if it's a network error (not a programmer error)
                if (error.name === 'TypeError' && error.message.includes('fetch')) {
                    consecutiveErrors++;
                    console.warn(`[ConnectionMonitor] Fetch error detected (${consecutiveErrors}/${errorThreshold}):`, error.message);

                    if (consecutiveErrors >= errorThreshold) {
                        console.error('[ConnectionMonitor] Multiple fetch failures detected, redirecting');
                        this.redirectToReloadingPage();
                    }
                }

                // Re-throw the error so calling code can handle it
                throw error;
            }
        };

        console.log('[ConnectionMonitor] Fetch interceptor installed');
    }

    /**
     * Check if we're currently on the reloading page
     */
    static isOnReloadingPage() {
        return window.location.pathname.includes('reloading.html');
    }

    /**
     * Initialize connection monitoring for the app
     * Call this from your main app initialization
     */
    static initialize() {
        // Don't monitor if we're on the reloading page itself
        if (ConnectionMonitor.isOnReloadingPage()) {
            console.log('[ConnectionMonitor] On reloading page, skipping initialization');
            return null;
        }

        const monitor = new ConnectionMonitor();
        monitor.start();
        return monitor;
    }
}

// Auto-initialize if loaded as a module
if (typeof window !== 'undefined') {
    // Make available globally for debugging
    window.ConnectionMonitor = ConnectionMonitor;
}
