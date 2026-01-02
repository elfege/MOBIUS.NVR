/**
 * Connection Monitor Module
 * Detects when the NVR server becomes unresponsive and redirects to reloading page
 */

export class ConnectionMonitor {
    constructor() {
        this.checkInterval = null;
        this.failedChecks = 0;
        this.maxFailedChecks = 2; // Fail after 2 consecutive failures (faster detection)
        this.checkIntervalMs = 5000; // Check every 5 seconds (more frequent)
        this.isMonitoring = false;

        console.log('[ConnectionMonitor] Initialized - will check health every', this.checkIntervalMs / 1000, 'seconds');
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
        const checkStartTime = Date.now();
        console.log(`[ConnectionMonitor] ⏱️ Performing health check at ${new Date().toISOString()}`);

        try {
            const response = await fetch('/api/health', {
                method: 'GET',
                cache: 'no-store',
                signal: AbortSignal.timeout(5000) // 5 second timeout
            });

            const latency = Date.now() - checkStartTime;
            console.log(`[ConnectionMonitor] ✅ Health check completed in ${latency}ms - Status: ${response.status}`);

            if (response.ok) {
                // Server is healthy, reset failure count
                if (this.failedChecks > 0) {
                    console.log('[ConnectionMonitor] 🎉 Server recovered after', this.failedChecks, 'failed checks');
                }
                this.failedChecks = 0;
            } else if (response.status === 503) {
                // Server is shutting down - immediate redirect
                console.warn('[ConnectionMonitor] ⚠️ Server returned 503 - parsing response...');
                try {
                    const data = await response.json();
                    console.log('[ConnectionMonitor] 📥 Shutdown response data:', data);
                    if (data.status === 'shutting_down') {
                        console.error('[ConnectionMonitor] 🛑 SERVER IS SHUTTING DOWN - REDIRECTING IMMEDIATELY');
                        this.redirectToReloadingPage();
                        return;
                    }
                } catch (jsonError) {
                    console.error('[ConnectionMonitor] ❌ Failed to parse 503 response JSON:', jsonError);
                    this.handleFailedCheck(`HTTP 503 (parse error: ${jsonError.message})`);
                }
            } else {
                this.handleFailedCheck(`HTTP ${response.status}`);
            }
        } catch (error) {
            const latency = Date.now() - checkStartTime;
            console.error(`[ConnectionMonitor] ❌ Health check FAILED after ${latency}ms:`, error.name, error.message);
            this.handleFailedCheck(error.message);
        }
    }

    /**
     * Handle a failed health check
     */
    handleFailedCheck(reason) {
        this.failedChecks++;
        console.warn(`[ConnectionMonitor] ⚠️ Health check FAILED (${this.failedChecks}/${this.maxFailedChecks}): ${reason}`);

        if (this.failedChecks >= this.maxFailedChecks) {
            console.error(`[ConnectionMonitor] 🚨 THRESHOLD REACHED: ${this.failedChecks} consecutive failures`);
            console.error('[ConnectionMonitor] 🔄 Server appears to be down - REDIRECTING TO RELOADING PAGE');
            this.redirectToReloadingPage();
        } else {
            console.log(`[ConnectionMonitor] 🔔 Will redirect after ${this.maxFailedChecks - this.failedChecks} more failure(s)`);
        }
    }

    /**
     * Redirect to the reloading page
     */
    redirectToReloadingPage() {
        console.log('[ConnectionMonitor] 📍 Current URL:', window.location.href);
        console.log('[ConnectionMonitor] 💾 Saving return URL to localStorage');

        // Stop monitoring to prevent multiple redirects
        this.stop();

        // Store current URL so we can return to it after reconnection
        localStorage.setItem('nvr_return_url', window.location.href);
        localStorage.setItem('nvr_reconnect_attempt', '1');

        console.log('[ConnectionMonitor] 🔀 Redirecting to /nginx/reloading.html');

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
        const errorThreshold = 3; // Redirect after 3 consecutive fetch errors (reduced for faster detection)

        console.log('[ConnectionMonitor] 🔌 Installing fetch interceptor (will trigger after', errorThreshold, 'consecutive errors)');

        // Store original fetch
        const originalFetch = window.fetch;

        // Override fetch
        window.fetch = async (...args) => {
            const url = args[0];
            try {
                const fetchStartTime = Date.now();
                const response = await originalFetch(...args);
                const fetchLatency = Date.now() - fetchStartTime;

                // Reset error counter on successful response
                if (response.ok) {
                    if (consecutiveErrors > 0) {
                        console.log(`[ConnectionMonitor] 🔌 Fetch to ${url} succeeded (${fetchLatency}ms) - resetting error count from ${consecutiveErrors}`);
                    }
                    consecutiveErrors = 0;
                } else {
                    console.warn(`[ConnectionMonitor] 🔌 Fetch to ${url} returned ${response.status} (${fetchLatency}ms)`);
                }

                return response;
            } catch (error) {
                console.error(`[ConnectionMonitor] 🔌 Fetch to ${url} FAILED:`, error.name, error.message);

                // Check if it's a network error (not a programmer error)
                if (error.name === 'TypeError' || error.message.includes('fetch') || error.message.includes('network')) {
                    consecutiveErrors++;
                    console.error(`[ConnectionMonitor] 🔌 Network error detected (${consecutiveErrors}/${errorThreshold})`);

                    if (consecutiveErrors >= errorThreshold) {
                        console.error(`[ConnectionMonitor] 🚨 FETCH ERROR THRESHOLD REACHED: ${consecutiveErrors} consecutive failures`);
                        console.error('[ConnectionMonitor] 🔄 Multiple fetch failures - REDIRECTING TO RELOADING PAGE');
                        this.redirectToReloadingPage();
                    } else {
                        console.log(`[ConnectionMonitor] 🔔 Will redirect after ${errorThreshold - consecutiveErrors} more fetch error(s)`);
                    }
                }

                // Re-throw the error so calling code can handle it
                throw error;
            }
        };

        console.log('[ConnectionMonitor] ✅ Fetch interceptor installed successfully');
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
