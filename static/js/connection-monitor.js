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

        // Detect device performance tier for appropriate thresholds
        // Returns: 'ultra-slow', 'slow', or 'normal'
        this.deviceTier = this._detectDeviceTier();

        // Adjust thresholds based on device capability tier
        // Ultra-slow devices (ancient iPads with aging batteries) get extremely lenient settings
        if (this.deviceTier === 'ultra-slow') {
            this.maxFailedChecks = 40;        // 40 failures before redirect
            this.checkIntervalMs = 30000;     // Check every 30 seconds
            this.healthCheckTimeoutMs = 45000; // 45 second timeout (aging WiFi adapters)
            this.fetchErrorThreshold = 30;    // 30 fetch errors
            console.log('[ConnectionMonitor] ULTRA-SLOW device detected - using very lenient thresholds');
        } else if (this.deviceTier === 'slow') {
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

        console.log('[ConnectionMonitor] Initialized - device tier:', this.deviceTier.toUpperCase(),
            '| check interval:', this.checkIntervalMs / 1000, 's',
            '| max failures:', this.maxFailedChecks,
            '| timeout:', this.healthCheckTimeoutMs / 1000, 's');
    }

    /**
     * Detect device performance tier based on various heuristics
     * Returns: 'ultra-slow', 'slow', or 'normal'
     *
     * Ultra-slow tier is for ancient devices with:
     * - Very old iOS/Android versions
     * - Aging batteries causing CPU throttling
     * - Poor WiFi adapters with high latency
     * - Very low CPU cores or memory
     */
    _detectDeviceTier() {
        const ua = navigator.userAgent.toLowerCase();

        // @ts-ignore - deviceMemory is not in all browsers
        const deviceMemory = navigator.deviceMemory;
        const cores = navigator.hardwareConcurrency;

        // @ts-ignore - connection API not in all browsers
        const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;

        // ========== ULTRA-SLOW TIER DETECTION ==========
        // Ancient iPads (iPad 2, 3, 4, Air 1, Mini 1-3) - these have aging batteries
        // and very slow WiFi adapters. iOS 9 and below indicates truly ancient hardware.
        const isAncientIPad = /ipad/.test(ua) && /os [4-9]_/.test(ua);

        // iPads running iOS 10-12 with very few cores are also ultra-slow
        // (iPad Air 1 has 2 cores, iPad 2/3/4 have 2 cores)
        const isVeryOldIPad = /ipad/.test(ua) &&
            /os 1[0-2]_/.test(ua) &&
            cores && cores <= 2;

        // Ancient Android tablets (Android 4-5) are ultra-slow
        const isAncientAndroid = /android [4-5]\./.test(ua) && /tablet|pad/i.test(ua);

        // Very low memory devices (< 1GB) are ultra-slow
        const veryLowMemory = deviceMemory && deviceMemory < 1;

        // 2G connection is ultra-slow
        const ultraSlowConnection = conn && (
            conn.effectiveType === 'slow-2g' ||
            conn.effectiveType === '2g'
        );

        // Any touch device with only 1-2 cores is ultra-slow
        const veryLowCores = 'ontouchstart' in window && cores && cores <= 2;

        // Check for ultra-slow tier
        const isUltraSlow = isAncientIPad || isVeryOldIPad || isAncientAndroid ||
                           veryLowMemory || ultraSlowConnection || veryLowCores;

        if (isUltraSlow) {
            console.log('[ConnectionMonitor] ULTRA-SLOW device indicators:', {
                isAncientIPad,
                isVeryOldIPad,
                isAncientAndroid,
                veryLowMemory,
                ultraSlowConnection,
                veryLowCores,
                hardwareConcurrency: cores,
                deviceMemory: deviceMemory,
                userAgent: ua.substring(0, 100) + '...'
            });
            return 'ultra-slow';
        }

        // ========== SLOW TIER DETECTION ==========
        // Older iPads (iOS 13-15 but still older hardware)
        const isOlderIPad = /ipad/.test(ua) && (
            /os 1[3-5]_/.test(ua) && cores && cores <= 4
        );

        // Older Android tablets (Android 6-8)
        const isOlderAndroidTablet = /android/.test(ua) && /tablet|pad/i.test(ua) && (
            /android [6-8]\./.test(ua) ||
            (cores && cores <= 4)
        );

        // Low memory devices (1-2GB)
        const lowMemory = deviceMemory && deviceMemory <= 2;

        // 3G connection
        const slowConnection = conn && (
            conn.effectiveType === '3g' ||
            conn.saveData === true
        );

        // Touch device with 3-4 cores
        const touchWithModerateCores = 'ontouchstart' in window && cores && cores <= 4;

        const isSlow = isOlderIPad || isOlderAndroidTablet || lowMemory ||
                       slowConnection || touchWithModerateCores;

        if (isSlow) {
            console.log('[ConnectionMonitor] Slow device indicators:', {
                isOlderIPad,
                isOlderAndroidTablet,
                lowMemory,
                slowConnection,
                touchWithModerateCores,
                hardwareConcurrency: cores,
                deviceMemory: deviceMemory,
                userAgent: ua.substring(0, 100) + '...'
            });
            return 'slow';
        }

        return 'normal';
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
