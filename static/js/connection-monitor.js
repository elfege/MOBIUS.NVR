/**
 * Connection Monitor Module
 * Detects when the NVR server becomes unresponsive and redirects to reloading page
 */

export class ConnectionMonitor {
    constructor() {
        this.checkInterval = null;
        this.failedChecks = 0;
        this.maxFailedChecks = 5; // Fail after 5 consecutive failures (less sensitive)
        this.checkIntervalMs = 10000; // Check every 10 seconds (less frequent)
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
                signal: AbortSignal.timeout(10000) // 10 second timeout (less sensitive)
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
     * Redirect to the reloading page (or show modal if completely offline)
     */
    async redirectToReloadingPage() {
        console.log('[ConnectionMonitor] 📍 Current URL:', window.location.href);
        console.log('[ConnectionMonitor] 💾 Saving return URL to localStorage');

        // Stop monitoring to prevent multiple redirects
        this.stop();

        // Store current URL so we can return to it after reconnection
        localStorage.setItem('nvr_return_url', window.location.href);
        localStorage.setItem('nvr_reconnect_attempt', '1');

        // Try to reach the reloading page first
        console.log('[ConnectionMonitor] 🔍 Checking if /reloading is accessible...');
        try {
            const response = await fetch('/reloading', {
                method: 'HEAD',
                cache: 'no-store',
                signal: AbortSignal.timeout(2000)
            });

            if (response.ok) {
                console.log('[ConnectionMonitor] ✅ /reloading is accessible, redirecting...');
                window.location.href = '/reloading';
            } else {
                console.warn('[ConnectionMonitor] ⚠️ /reloading returned', response.status, '- showing modal instead');
                this.showOfflineModal();
            }
        } catch (error) {
            console.error('[ConnectionMonitor] ❌ Cannot reach /reloading:', error.message);
            console.log('[ConnectionMonitor] 📋 Showing offline modal instead');
            this.showOfflineModal();
        }
    }

    /**
     * Show an inline modal when server is completely unreachable
     */
    showOfflineModal() {
        console.log('[ConnectionMonitor] 🎨 Creating offline modal');

        // Create modal overlay
        const modal = document.createElement('div');
        modal.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.95);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 999999;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        `;

        // Create modal content
        modal.innerHTML = `
            <div style="
                background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
                padding: 3rem;
                border-radius: 12px;
                text-align: center;
                max-width: 500px;
                box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
            ">
                <div style="
                    width: 60px;
                    height: 60px;
                    border: 4px solid rgba(255,255,255,0.1);
                    border-top-color: #ff9800;
                    border-radius: 50%;
                    animation: spin 1s linear infinite;
                    margin: 0 auto 1.5rem;
                "></div>
                <h1 style="
                    font-size: 1.5rem;
                    font-weight: 500;
                    margin-bottom: 0.5rem;
                    color: #fff;
                ">Server Unavailable</h1>
                <p style="
                    color: #ffb74d;
                    font-style: italic;
                    margin-bottom: 1rem;
                ">Connection to NVR server lost</p>
                <p style="
                    color: #888;
                    margin-bottom: 1.5rem;
                ">Attempting to reconnect...</p>
                <button onclick="try { location.reload(true); } catch(e) { location.reload(); }" style="
                    background: transparent;
                    border: 1px solid #ff9800;
                    color: #ff9800;
                    padding: 0.5rem 1.5rem;
                    border-radius: 20px;
                    cursor: pointer;
                    font-size: 0.9rem;
                    transition: all 0.2s;
                " onmouseover="this.style.background='#ff9800'; this.style.color='#fff';"
                   onmouseout="this.style.background='transparent'; this.style.color='#ff9800';">
                    Retry Now
                </button>
            </div>
            <style>
                @keyframes spin {
                    to { transform: rotate(360deg); }
                }
            </style>
        `;

        document.body.appendChild(modal);
        console.log('[ConnectionMonitor] ✅ Offline modal displayed');

        // Retry every 5 seconds
        const retryInterval = setInterval(async () => {
            console.log('[ConnectionMonitor] 🔄 Retrying connection...');
            try {
                const response = await fetch('/api/health', {
                    method: 'GET',
                    cache: 'no-store',
                    signal: AbortSignal.timeout(3000)
                });

                if (response.ok) {
                    console.log('[ConnectionMonitor] 🎉 Server is back online, reloading page');
                    clearInterval(retryInterval);
                    // Clear localStorage flags
                    localStorage.removeItem('nvr_return_url');
                    localStorage.removeItem('nvr_reconnect_attempt');
                    // Hard reload to bypass cache
                    try {
                        location.reload(true);
                    } catch (error) {
                        location.reload();
                    }
                }
            } catch (error) {
                console.log('[ConnectionMonitor] ⏳ Still offline, will retry in 5s');
            }
        }, 5000);
    }

    /**
     * Setup global fetch interceptor to detect network errors
     * This catches errors that happen during API calls from other parts of the app
     */
    setupFetchInterceptor() {
        // Track consecutive fetch errors
        let consecutiveErrors = 0;
        const errorThreshold = 8; // Redirect after 8 consecutive fetch errors (less sensitive)

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
        return window.location.pathname.includes('reloading');
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
