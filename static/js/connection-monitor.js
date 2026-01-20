/**
 * Connection Monitor Module
 * Detects when the NVR server becomes unresponsive and redirects to reloading page
 */

export class ConnectionMonitor {
    constructor() {
        this.checkInterval = null;
        this.retryInterval = null;  // Track retry interval to prevent duplicates
        this.failedChecks = 0;
        this.isMonitoring = false;
        this.isRedirecting = false;  // Guard against duplicate redirects/modals
        this.modalShown = false;  // Track if modal is already displayed

        // Detect if device is likely a slower/older tablet or mobile device
        // These devices may have slower network handling and need more lenient thresholds
        this.isSlowDevice = this._detectSlowDevice();

        // Adjust thresholds based on device capability
        // Slow devices get much more lenient settings to avoid false positives
        if (this.isSlowDevice) {
            this.maxFailedChecks = 20;        // 20 failures before redirect (was 10)
            this.checkIntervalMs = 15000;     // Check every 15 seconds (was 10)
            this.healthCheckTimeoutMs = 20000; // 20 second timeout (was 10)
            this.fetchErrorThreshold = 15;    // 15 fetch errors (was 8)
            console.log('[ConnectionMonitor] Slow device detected - using lenient thresholds');
        } else {
            this.maxFailedChecks = 10;        // Standard: 10 failures
            this.checkIntervalMs = 10000;     // Standard: 10 seconds
            this.healthCheckTimeoutMs = 10000; // Standard: 10 second timeout
            this.fetchErrorThreshold = 8;     // Standard: 8 fetch errors
        }

        console.log('[ConnectionMonitor] Initialized - device:', this.isSlowDevice ? 'SLOW' : 'NORMAL',
            '| check interval:', this.checkIntervalMs / 1000, 's',
            '| max failures:', this.maxFailedChecks,
            '| timeout:', this.healthCheckTimeoutMs / 1000, 's');
    }

    /**
     * Detect if device is likely slower/older based on various heuristics
     * Returns true for older tablets, low-end mobile devices, etc.
     */
    _detectSlowDevice() {
        const ua = navigator.userAgent.toLowerCase();

        // Check for older iPad models (iPad 1-4, iPad Air 1, iPad mini 1-3)
        // These have older A-series chips and slower performance
        const isOlderIPad = /ipad/.test(ua) && (
            // iOS versions before 13 typically indicate older hardware
            /os [4-9]_|os 1[0-2]_/.test(ua) ||
            // Check for hardware concurrency (CPU cores) - older iPads have fewer
            (navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 2)
        );

        // Check for older Android tablets
        const isOlderAndroidTablet = /android/.test(ua) && /tablet|pad/i.test(ua) && (
            // Android versions before 8 are typically on older hardware
            /android [4-7]\./.test(ua) ||
            // Low CPU core count
            (navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 2)
        );

        // Check device memory (Chrome/Firefox on Android) - low memory = slow device
        // @ts-ignore - deviceMemory is not in all browsers
        const lowMemory = navigator.deviceMemory && navigator.deviceMemory <= 2;

        // Check connection type if available - slow connections need more tolerance
        // @ts-ignore - connection API not in all browsers
        const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
        const slowConnection = conn && (
            conn.effectiveType === 'slow-2g' ||
            conn.effectiveType === '2g' ||
            conn.effectiveType === '3g' ||
            conn.saveData === true
        );

        // Check for any touch device (tablets/phones) with low cores
        const isTouchWithLowCores = 'ontouchstart' in window &&
            navigator.hardwareConcurrency &&
            navigator.hardwareConcurrency <= 4;

        // Combine all checks - if any indicate slow device, be lenient
        const isSlow = isOlderIPad || isOlderAndroidTablet || lowMemory || slowConnection || isTouchWithLowCores;

        if (isSlow) {
            console.log('[ConnectionMonitor] Slow device indicators:', {
                isOlderIPad,
                isOlderAndroidTablet,
                lowMemory,
                slowConnection,
                isTouchWithLowCores,
                hardwareConcurrency: navigator.hardwareConcurrency,
                // @ts-ignore
                deviceMemory: navigator.deviceMemory,
                userAgent: ua.substring(0, 100) + '...'
            });
        }

        return isSlow;
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
     * Clears both the main health check interval and any retry intervals
     */
    stop() {
        if (this.checkInterval) {
            clearInterval(this.checkInterval);
            this.checkInterval = null;
        }
        if (this.retryInterval) {
            clearInterval(this.retryInterval);
            this.retryInterval = null;
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
                signal: AbortSignal.timeout(this.healthCheckTimeoutMs)
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
     * Protected by isRedirecting flag to prevent duplicate calls from
     * concurrent health check failures and fetch interceptor errors
     */
    async redirectToReloadingPage() {
        // Guard against duplicate calls - critical to prevent multiple modals/intervals
        if (this.isRedirecting) {
            console.log('[ConnectionMonitor] Already handling redirect, ignoring duplicate call');
            return;
        }
        this.isRedirecting = true;

        console.log('[ConnectionMonitor] Current URL:', window.location.href);
        console.log('[ConnectionMonitor] Saving return URL to localStorage');

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
     * Protected by modalShown flag to prevent duplicate modals
     */
    showOfflineModal() {
        // Guard against duplicate modals - each modal spawns its own retry interval
        if (this.modalShown) {
            console.log('[ConnectionMonitor] Modal already displayed, ignoring duplicate call');
            return;
        }
        this.modalShown = true;

        console.log('[ConnectionMonitor] Creating offline modal');

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
                <button onclick="window.location.replace(window.location.pathname + '?_t=' + Date.now())" style="
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
        console.log('[ConnectionMonitor] Offline modal displayed');

        // Clear any existing retry interval before creating new one (extra safety)
        if (this.retryInterval) {
            clearInterval(this.retryInterval);
        }

        // Retry every 5 seconds - store on this for cleanup via stop()
        this.retryInterval = setInterval(async () => {
            console.log('[ConnectionMonitor] Retrying connection...');
            try {
                const response = await fetch('/api/health', {
                    method: 'GET',
                    cache: 'no-store',
                    signal: AbortSignal.timeout(3000)
                });

                if (response.ok) {
                    console.log('[ConnectionMonitor] Server is back online, reloading page');
                    clearInterval(this.retryInterval);
                    this.retryInterval = null;
                    // Clear localStorage flags
                    localStorage.removeItem('nvr_return_url');
                    localStorage.removeItem('nvr_reconnect_attempt');
                    // Reset flags before reload
                    this.modalShown = false;
                    this.isRedirecting = false;
                    // Reload with cache-busting param for iOS Safari
                    // (location.reload(true) is deprecated and ignored by iOS)
                    const url = new URL(window.location.href);
                    url.searchParams.set('_t', Date.now());
                    window.location.replace(url.pathname + url.search);
                }
            } catch (error) {
                console.log('[ConnectionMonitor] Still offline, will retry in 5s');
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
        const errorThreshold = this.fetchErrorThreshold;

        console.log('[ConnectionMonitor] 🔌 Installing fetch interceptor (will trigger after', errorThreshold, 'consecutive errors)');

        // Store original fetch
        const originalFetch = window.fetch;

        // Override fetch
        window.fetch = async (...args) => {
            const url = typeof args[0] === 'string' ? args[0] : args[0]?.url || '';

            // Skip monitoring for media/streaming URLs - these fail for content reasons, not connection issues
            const isMediaUrl = url.includes('.m3u8') ||
                               url.includes('.ts') ||
                               url.includes('.m4s') ||
                               url.includes('.mp4') ||
                               url.includes('/hls/') ||
                               url.includes('/whep');

            try {
                const fetchStartTime = Date.now();
                const response = await originalFetch(...args);
                const fetchLatency = Date.now() - fetchStartTime;

                // Reset error counter on successful response (skip media URLs)
                if (response.ok && !isMediaUrl) {
                    if (consecutiveErrors > 0) {
                        console.log(`[ConnectionMonitor] 🔌 Fetch to ${url} succeeded (${fetchLatency}ms) - resetting error count from ${consecutiveErrors}`);
                    }
                    consecutiveErrors = 0;
                } else if (!response.ok && !isMediaUrl) {
                    console.warn(`[ConnectionMonitor] 🔌 Fetch to ${url} returned ${response.status} (${fetchLatency}ms)`);
                }

                return response;
            } catch (error) {
                // Skip media URL errors - these are stream errors, not connection errors
                if (isMediaUrl) {
                    throw error;
                }

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
