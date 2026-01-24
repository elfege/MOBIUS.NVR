/**
 * Multi-Stream Manager - ES6 + jQuery
 * Orchestrates HLS and MJPEG stream managers for unified camera viewing
 *
 * MJPEG modes:
 * - HTTP MJPEG (default): One HTTP connection per camera stream
 *   - Limited by browser's ~6 connections per domain
 *   - Causes queuing/slow loading with 16+ cameras
 *
 * - WebSocket MJPEG (opt-in via ?useWebSocketMJPEG=true):
 *   - All cameras multiplexed over single WebSocket connection
 *   - Bypasses browser connection limit for instant loading
 *   - Server sends base64-encoded JPEG frames with camera ID prefixes
 */

import { PTZController } from '../controllers/ptz-controller.js';
import { FLVStreamManager } from './flv-stream.js';
import { makeHealthMonitor } from './health.js';
import { HLSStreamManager } from './hls-stream.js';
import { MJPEGStreamManager } from './mjpeg-stream.js';
import { WebRTCStreamManager } from './webrtc-stream.js';
import { CameraStateMonitor } from './camera-state-monitor.js';
import { WebSocketMJPEGStreamManager } from './websocket-mjpeg-stream.js';
import { SnapshotStreamManager } from './snapshot-stream.js';

/**
 * Detect iOS devices (iPhone, iPad, iPod)
 * iOS Safari requires encrypted WebRTC (DTLS-SRTP).
 * If DTLS is enabled in cameras.json (webrtc_global_settings.enable_dtls),
 * iOS can use WebRTC for ~200ms latency instead of HLS (~2-4s).
 *
 * Detection logic:
 * 1. Explicit iOS user agent strings (iPhone, iPad, iPod)
 * 2. iPadOS requesting desktop site: MacIntel platform + touch support + NOT a Mac
 *    (Macs with Touch Bar have maxTouchPoints > 0 but are NOT iOS)
 *
 * We distinguish iPadOS from Mac by checking for touch-primary behavior:
 * - iPadOS: maxTouchPoints >= 5 (multi-touch screen)
 * - Mac with Touch Bar: maxTouchPoints == 1 or 2 (Touch Bar only)
 */
function isIOSDevice() {
    // Explicit iOS user agent (covers most cases)
    if (/iPad|iPhone|iPod/.test(navigator.userAgent)) {
        return true;
    }

    // iPadOS 13+ requests desktop sites and reports as MacIntel
    // But it has full multi-touch support (5+ touch points for gestures)
    // Mac with Touch Bar only has 1-2 touch points
    if (navigator.platform === 'MacIntel' && navigator.maxTouchPoints >= 5) {
        return true;
    }

    return false;
}

/**
 * Cache for streaming configuration fetched from backend.
 * Loaded once on page load to avoid repeated API calls.
 */
let _streamingConfigCache = null;

/**
 * Fetch streaming configuration from backend (cached).
 * Returns WebRTC settings including DTLS encryption status.
 *
 * @returns {Promise<{webrtc: {encryption_enabled: boolean, ice_servers: Array}}>}
 */
async function getStreamingConfig() {
    if (_streamingConfigCache !== null) {
        return _streamingConfigCache;
    }

    try {
        const response = await fetch('/api/config/streaming');
        if (response.ok) {
            _streamingConfigCache = await response.json();
            console.log('[Config] Streaming config loaded:', _streamingConfigCache);
        } else {
            console.warn('[Config] Failed to fetch streaming config, using defaults');
            _streamingConfigCache = { webrtc: { encryption_enabled: false, ice_servers: [] } };
        }
    } catch (e) {
        console.error('[Config] Error fetching streaming config:', e);
        _streamingConfigCache = { webrtc: { encryption_enabled: false, ice_servers: [] } };
    }

    return _streamingConfigCache;
}

/**
 * Check if WebRTC DTLS encryption is enabled on the server.
 * When enabled, iOS Safari can use WebRTC instead of falling back to HLS.
 *
 * @returns {Promise<boolean>}
 */
async function isDTLSEnabled() {
    const config = await getStreamingConfig();
    return config?.webrtc?.encryption_enabled === true;
}

/**
 * Check if user has enabled WebRTC for fullscreen mode.
 * This setting is controlled via Settings UI toggle.
 *
 * WebRTC offers lower latency (~200ms) but HLS is more stable and higher quality.
 * Default is HLS (returns false) when no preference is set.
 *
 * @returns {boolean}
 */
function isFullscreenWebRTCEnabled() {
    return localStorage.getItem('fullscreenStreamType') === 'webrtc';
}

/**
 * Check if user has enabled snapshot polling for grid view (desktop only).
 * This setting is controlled via Settings UI toggle.
 *
 * Snapshot mode polls /api/snap/<camera_id> at ~1 fps, reducing CPU/bandwidth.
 * iOS Safari always uses this mode automatically due to MJPEG/video limitations.
 * This allows desktop users to opt-in for the same lightweight behavior.
 *
 * @returns {boolean}
 */
function isGridSnapshotsEnabled() {
    return localStorage.getItem('gridSnapshotsOnly') === 'true';
}

/**
 * Check if iOS should force WebRTC in grid view instead of snapshots.
 * This is an EXPERIMENTAL option that allows iOS users to get real-time
 * video (~200ms latency) in grid view instead of 1fps snapshot polling.
 *
 * WARNING: This may cause issues on iOS due to:
 * - Higher resource usage (CPU/memory/battery)
 * - Safari limits concurrent video decodes (~4-8 streams)
 * - Less reliable than snapshot polling
 * - May cause black screens or freezes with many cameras
 *
 * Requires DTLS to be enabled on the server for iOS WebRTC support.
 *
 * @returns {boolean}
 */
function isForceWebRTCGridEnabled() {
    return localStorage.getItem('forceWebRTCGrid') === 'true';
}

/**
 * Detect portable/mobile devices that should use MJPEG for grid view.
 * These devices have limited resources and benefit from MJPEG's lighter decode overhead.
 * MJPEG uses simple <img> tags instead of <video> elements, avoiding:
 * - iOS Safari's simultaneous video decode limits (~4-8 streams)
 * - Mobile devices' memory/CPU constraints
 *
 * Returns true for:
 * - iOS devices (iPhone, iPad, iPod)
 * - Android mobile devices
 * - Any touch-enabled mobile device
 */
function isPortableDevice() {
    // iOS devices
    if (isIOSDevice()) return true;

    // Android mobile/tablet
    if (/Android/i.test(navigator.userAgent)) return true;

    // Generic mobile detection (webOS, BlackBerry, Windows Phone, etc.)
    if (/webOS|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent)) return true;

    // Touch device with small screen (likely mobile)
    if ('ontouchstart' in window && window.innerWidth < 1024) return true;

    return false;
}


export class MultiStreamManager {
    constructor() {
        this.mjpegManager = new MJPEGStreamManager();
        this.hlsManager = new HLSStreamManager();
        this.flvManager = new FLVStreamManager();
        this.webrtcManager = new WebRTCStreamManager();
        this.ptzController = new PTZController();

        // Snapshot manager for iOS grid view - polls single JPEGs instead of MJPEG streams
        // Much more reliable on iOS Safari than multipart MJPEG
        this.snapshotManager = new SnapshotStreamManager();

        // WebSocket MJPEG manager for multiplexed streaming
        // Bypasses browser's ~6 connection limit for faster loading with many cameras
        // Enable via ?useWebSocketMJPEG=true URL parameter
        this.wsMjpegManager = new WebSocketMJPEGStreamManager();
        this.useWebSocketMJPEG = new URLSearchParams(window.location.search).get('useWebSocketMJPEG') === 'true';

        // iOS pagination state - limit streams per page to avoid Safari video decode limits
        // DISABLED: Now that iOS uses snapshot polling (img tags) instead of video elements,
        // the Safari video decode limit no longer applies. All cameras can load simultaneously.
        // Keeping the structure for potential future use with HLS on iOS.
        this.iosPagination = {
            enabled: false,  // Disabled - snapshots don't have video decode limits
            camerasPerPage: 6,
            currentPage: 0,
            totalPages: 0,
            allCameraIds: []  // All camera IDs in order
        };

        // CameraStateMonitor polls backend state for status display
        // Recovery callback is enabled/disabled dynamically based on WebSocket connection state:
        // - WebSocket connected: callback disabled (WebSocket provides instant notifications)
        // - WebSocket disconnected: callback enabled (10-second polling fallback)
        this.wsRecoveryEnabled = false;  // Track if WebSocket is handling recovery
        this.cameraStateMonitor = new CameraStateMonitor({
            onRecovery: (cameraId, $streamItem, previousState, newState) => {
                // CRITICAL: Skip recovery for user-stopped streams
                // User explicitly stopped this stream via UI, don't auto-restart
                if (this.isUserStoppedStream(cameraId)) {
                    console.log(`[Recovery] ${cameraId}: Skipping recovery - user manually stopped this stream`);
                    return;
                }

                // When WebSocket is active, still check if this WebRTC stream needs help
                // The WebSocket notification fires BEFORE FFmpeg is ready, but poll-based
                // recovery fires when backend actually shows 'online' (MediaMTX has stream)
                if (this.wsRecoveryEnabled) {
                    const streamType = $streamItem.data('stream-type');
                    const videoElement = $streamItem.find('.stream-video')[0];

                    // For WebRTC streams that are still black, use poll-based as secondary fallback
                    // This catches the case where WebSocket notified too early and fallback also failed
                    if (streamType === 'WEBRTC' && videoElement &&
                        (videoElement.readyState < 2 || videoElement.videoWidth === 0)) {
                        console.log(`[Recovery] ${cameraId}: Poll-based secondary recovery for black WebRTC stream (${previousState} → ${newState})`);
                        this.handleBackendRecovery(cameraId, $streamItem);
                        return;
                    }

                    console.log(`[Recovery] ${cameraId}: Skipping poll-based recovery (WebSocket active, stream OK)`);
                    return;
                }
                console.log(`[Recovery] ${cameraId}: Poll-based recovery (WebSocket down) - ${previousState} → ${newState}`);
                this.handleBackendRecovery(cameraId, $streamItem);
            }
        });
        // Arrow function preserves context
        this.getCameraConfig = (id) => this.hlsManager.getCameraConfig(id);
        this.buildHlsConfig = (config, isLL) => this.hlsManager.buildHlsConfig(config, isLL);

        // Cache jQuery selectors
        this.$container = $('#streams-container');
        this.$streamCount = $('#stream-count');


        this.restartAttempts = new Map(); // Track restart attempts per camera
        this.restartTimers = new Map();   // Track pending retry timers
        this.recentFailures = new Map();  // Track failure history for escalating recovery

        const H = window.UI_HEALTH || {};
        console.log(`UI HEALTH ENV: ${JSON.stringify(H)}`)
        // Only create health monitor if enabled
        if (H.uiHealthEnabled) {
            this.health = makeHealthMonitor({
                blankThreshold: (H.blankAvg != null && H.blankStd != null)
                    ? { avg: H.blankAvg, std: H.blankStd }  // cameras.json flat format
                    : (H.blankThreshold || { avg: 12, std: 5 }),  // .env nested format or default
                sampleIntervalMs: H.sampleIntervalMs ?? 6000,
                staleAfterMs: H.staleAfterMs ?? 20000,
                consecutiveBlankNeeded: H.consecutiveBlankNeeded ?? 10,
                cooldownMs: H.cooldownMs ?? 60000,
                warmupMs: H.warmupMs ?? 60000,
                onUnhealthy: async ({ cameraId, reason, metrics }) => {
                    console.warn(`[Health] Stream unhealthy: ${cameraId}, reason: ${reason}`, metrics);

                    const $streamItem = $(`.stream-item[data-camera-serial="${cameraId}"]`);
                    if (!$streamItem.length) return;

                    // Get camera metadata for nuclear recovery
                    const cameraType = $streamItem.data('camera-type');
                    const streamType = $streamItem.data('stream-type');

                    // Get restart attempt count
                    const attempts = this.restartAttempts.get(cameraId) || 0;
                    const maxAttempts = H.maxAttempts ?? 10;  // 0 = infinite

                    // Check if max attempts reached (skip check if maxAttempts is 0)
                    if (maxAttempts > 0 && attempts >= maxAttempts) {
                        console.error(`[Health] ${cameraId}: Max restart attempts (${maxAttempts}) reached`);
                        this.setStreamStatus($streamItem, 'failed', `Failed after ${maxAttempts} attempts`);
                        return;
                    }

                    // Track failure history for escalating recovery
                    const now = Date.now();
                    const history = this.recentFailures.get(cameraId) || {
                        timestamps: [],
                        lastMethod: null
                    };

                    // Clean old failures (>60s ago)
                    history.timestamps = history.timestamps.filter(t => now - t < 60000);
                    history.timestamps.push(now);

                    // Determine recovery method: first 3 tries = refresh, then nuclear
                    const recentFailureCount = history.timestamps.length;
                    const method = (recentFailureCount <= 3) ? 'refresh' : 'nuclear';
                    history.lastMethod = method;
                    this.recentFailures.set(cameraId, history);

                    // Exponential backoff: 5s, 10s, 20s, 40s, 60s (max)
                    const delay = Math.min(5000 * Math.pow(2, attempts), 60000);

                    const methodLabel = method === 'refresh' ? 'Refresh' : 'Nuclear Stop+Start';
                    console.log(`[Health] ${cameraId}: Scheduling ${methodLabel} restart ${attempts + 1}/${maxAttempts > 0 ? maxAttempts : '∞'} in ${delay / 1000}s (failures in 60s: ${recentFailureCount})`);
                    this.setStreamStatus($streamItem, 'loading', `${methodLabel} retry ${attempts + 1} in ${delay / 1000}s`);

                    // Increment counter
                    this.restartAttempts.set(cameraId, attempts + 1);

                    // Clear existing timer
                    if (this.restartTimers.has(cameraId)) {
                        clearTimeout(this.restartTimers.get(cameraId));
                    }

                    // Schedule restart with appropriate method
                    const timer = setTimeout(async () => {
                        this.restartTimers.delete(cameraId);
                        console.log(`[Health] ${cameraId}: Executing ${methodLabel} attempt ${attempts + 1}`);

                        if (method === 'refresh') {
                            // Standard refresh - works for transient issues
                            await this.restartStream(cameraId, $streamItem);
                        } else {
                            await this.nuclear(cameraId, streamItem, cameraType, streamType)
                        }
                    }, delay);

                    this.restartTimers.set(cameraId, timer);
                }
            });
            console.log("UI HEALTH CHECK ENABLED");
        } else {
            this.health = null;  // Set to null so we know it's disabled
            console.log("UI HEALTH CHECK DISABLED");
        }

        this.init();
    }

    init() {
        console.log('========== MultiStreamManager INIT() CALLED ==========');
        this.setupLayout();
        this.setupEventListeners();
        this.updateStreamCount();

        // Initialize iOS pagination if enabled
        if (this.iosPagination.enabled) {
            this.initIOSPagination();
        }

        // Pre-fetch streaming config (caches DTLS setting for iOS WebRTC check)
        // This runs in parallel with stream loading so config is ready when needed
        getStreamingConfig().then(config => {
            console.log(`[Init] Streaming config: DTLS=${config?.webrtc?.encryption_enabled}`);
        }).catch(err => {
            console.warn('[Init] Failed to pre-fetch streaming config:', err);
        });

        // Start all streams (fire and forget - don't await)
        console.log('[Init] Starting streams in background...');
        this.startAllStreams().then(() => {
            console.log('[Init] All streams completed');
        }).catch(err => {
            console.error('[Init] Stream loading error:', err);
        });

        // Start camera state monitor (polls CameraStateTracker API every 10s)
        console.log('[Init] Starting camera state monitor...');
        this.cameraStateMonitor.start();

        // Connect to stream events WebSocket for instant restart notifications
        // This allows frontend to refresh HLS immediately when backend restarts a stream
        console.log('[Init] Connecting to stream events WebSocket...');
        this.connectStreamEventsSocket();

        // Restore fullscreen independently after short delay
        // Just needs DOM to be ready, doesn't need streams loaded
        setTimeout(() => {
            console.log('[Init] Attempting fullscreen restore...');
            this.restoreFullscreenFromLocalStorage();
        }, 1000);  // Reduced from 3000ms - just needs DOM ready
    }

    async nuclear(cameraId, streamItem, cameraType, streamType) {
        // Nuclear option - forces backend to restart FFmpeg
        console.log(`[Health] ${cameraId}: Nuclear recovery - forcing UI stop+start cycle`);

        // Step 1: UI stop (client-side only, no backend call)
        await this.stopIndividualStream(cameraId, $streamItem, cameraType, streamType);

        // Step 2: Wait for backend to notice stream is gone
        await new Promise(r => setTimeout(r, 3000));

        // Step 3: UI start (forces backend to create new FFmpeg)
        this.setStreamStatus($streamItem, 'loading', 'Nuclear restart...');
        const success = await this.startStream(cameraId, $streamItem, cameraType, streamType);

        if (success) {
            // Success path already handled in startStream
            console.log(`[Health] ${cameraId}: Nuclear restart succeeded`);
            // Clear failure history on success
            this.recentFailures.delete(cameraId);
            this.restartAttempts.delete(cameraId);
        } else {
            console.error(`[Health] ${cameraId}: Nuclear restart failed`);
        }
    }

    setupLayout() {
        const $streamItems = this.$container.find('.stream-item');
        const count = $streamItems.length;

        // Calculate optimal grid layout
        let cols;
        if (count <= 1) cols = 1;
        else if (count <= 4) cols = 2;
        else if (count <= 9) cols = 3;
        else if (count <= 16) cols = 4;
        else cols = 5;

        // this.$container.attr('class', `streams-container grid-${cols}`);
        // This preserves existing classes (including grid-attached) while only swapping the grid column class.
        this.$container.removeClass('grid-1 grid-2 grid-3 grid-4 grid-5').addClass(`grid-${cols}`);


        // Set aspect ratio for each stream
        $streamItems.css('aspect-ratio', '16/9');
    }

    setupEventListeners() {
        // Fullscreen button click handler - uses event namespace for clean binding
        // IMPORTANT: Ensure only ONE MultiStreamManager instance exists. This class auto-instantiates
        // at the bottom of this file via $(document).ready(). Do NOT create additional instances in
        // HTML inline scripts or other modules.
        // 
        // To verify single instance: console.log($._data($('#streams-container')[0], 'events'))
        // should show only ONE handler per selector.
        // 
        // HISTORICAL NOTE: Previously used .off() as safety net to remove duplicate handlers:
        // this.$container.off('click.fullscreen', '.stream-fullscreen-btn');
        // This was masking the root cause (duplicate instantiation in streams.html inline scripts).
        // Now fixed - module handles its own instantiation, HTML just imports it.

        // Add single handler with namespace
        this.$container.on('click.fullscreen', '.stream-fullscreen-btn', async (e) => {
            e.stopPropagation();
            e.preventDefault();

            // // Global lock to prevent ANY instance from processing simultaneously
            // if (window._fullscreenLocked) {
            //     console.log('[Fullscreen] BLOCKED - global lock active');
            //     return;
            // }

            // Simple debounce - ignore rapid clicks
            if (this._fullscreenProcessing) {
                console.log('[Fullscreen] Processing - ignoring rapid click');
                return;
            }

            this._fullscreenProcessing = true;

            window._fullscreenLocked = true;
            console.log('[Fullscreen] Lock acquired');

            try {
                // Check current state
                const isCurrentlyFullscreen = $('.stream-item.css-fullscreen').length > 0;
                console.log(`[Fullscreen] Button clicked - state: ${isCurrentlyFullscreen ? 'FULLSCREEN' : 'GRID'}`);

                if (isCurrentlyFullscreen) {
                    // Exit
                    await this.closeFullscreen();
                    console.log('[Fullscreen] Exit complete');
                } else {
                    // Enter
                    const $streamItem = $(e.target).closest('.stream-item');
                    if (!$streamItem.length) return;

                    const cameraId = $streamItem.data('camera-serial');
                    const name = $streamItem.data('camera-name');
                    const cameraType = $streamItem.data('camera-type');
                    const streamType = $streamItem.data('stream-type');

                    await this.openFullscreen(cameraId, name, cameraType, streamType);
                    console.log('[Fullscreen] Enter complete');
                }

            } catch (error) {
                console.error('[Fullscreen] Toggle error:', error);
            } finally {
                // // Release lock after 1 second delay
                // setTimeout(() => {
                //     window._fullscreenLocked = false;
                //     console.log('[Fullscreen] Lock released');
                // }, 1000);
                // Release processing flag
                setTimeout(() => {
                    this._fullscreenProcessing = false;
                }, 300);  // Shorter delay since no race conditions
            }
        });

        // Global control handlers
        $('#start-all-streams').on('click', () => {
            this.startAllStreams();
        });

        $('#stop-all-streams').on('click', () => {
            this.stopAllStreams();
        });

        // Individual stream control handlers
        this.$container.on('click', '.start-stream-btn', (e) => {
            e.stopPropagation();
            const $streamItem = $(e.target).closest('.stream-item');
            const cameraId = $streamItem.data('camera-serial');
            const cameraType = $streamItem.data('camera-type');
            const streamType = $streamItem.data('stream-type');
            this.startStream(cameraId, $streamItem, cameraType, streamType);
        });

        // =========================================================================
        // STOP STREAM BUTTON HANDLER
        // =========================================================================
        // IMPORTANT: NEVER use $(stop-stream-btn).click() or .trigger('click') programmatically!
        //
        // This handler sets userInitiated=true which marks the stream as "user-stopped"
        // in localStorage. This prevents the watchdog/health monitor from auto-restarting
        // the stream. If you programmatically trigger this click, you'll mark streams as
        // user-stopped when the user didn't actually intend to stop them.
        //
        // For programmatic stops (recovery, page switches, etc.), call:
        //   this.stopIndividualStream(cameraId, $streamItem, cameraType, streamType)
        // WITHOUT the { userInitiated: true } option.
        // =========================================================================
        this.$container.on('click', '.stop-stream-btn', (e) => {
            e.stopPropagation();
            const $streamItem = $(e.target).closest('.stream-item');
            const cameraId = $streamItem.data('camera-serial');
            const cameraType = $streamItem.data('camera-type');
            const streamType = $streamItem.data('stream-type');
            // Pass userInitiated=true so we track this as user-stopped and don't auto-restart
            this.stopIndividualStream(cameraId, $streamItem, cameraType, streamType, { userInitiated: true });
        });

        // PTZ control handlers
        this.$container.on('click', '.ptz-btn', (e) => {
            e.stopPropagation();
            const $button = $(e.target);
            const direction = $button.data('direction');
            const $streamItem = $button.closest('.stream-item');

            if (direction && $streamItem.length) {
                const cameraSerial = $streamItem.data('camera-serial');
                this.executePTZ(cameraSerial, direction, $button);
            }
        });

        // Refresh stream handler
        this.$container.on('click', '.refresh-stream-btn', (e) => {
            e.stopPropagation();
            const $streamItem = $(e.target).closest('.stream-item');
            const cameraId = $streamItem.data('camera-serial');
            const streamType = $streamItem.data('stream-type');
            const videoElement = $streamItem.find('.stream-video')[0];

            // Only HLS and WebRTC streams can be force-refreshed (for now)
            // not adding this condition will make the system
            // create a new rtsp stream while rtmp witll still
            // be running.
            if (streamType === 'HLS' || streamType === 'LL_HLS' || streamType === 'NEOLINK' || streamType === 'NEOLINK_LL_HLS') {
                this.hlsManager.forceRefreshStream(cameraId, videoElement);
            } else if (streamType === 'WEBRTC') {
                this.webrtcManager.forceRefreshStream(cameraId, videoElement);
            }
        });

        // Track cameras currently undergoing manual restart (to block WebSocket auto-recovery)
        this.manualRestartInProgress = this.manualRestartInProgress || new Set();

        // Restart stream handler (backend FFmpeg restart)
        // Unlike refresh which just reconnects HLS.js, this kills and restarts FFmpeg
        this.$container.on('click', '.restart-stream-btn', async (e) => {
            e.stopPropagation();
            const $streamItem = $(e.target).closest('.stream-item');
            const cameraId = $streamItem.data('camera-serial');
            const streamType = $streamItem.data('stream-type');
            const videoElement = $streamItem.find('.stream-video')[0];

            // MJPEG streams don't support restart (stateless)
            if (streamType === 'MJPEG') {
                console.log(`[Restart] ${cameraId}: MJPEG streams do not support restart`);
                return;
            }

            // Mark this camera as undergoing manual restart
            // This prevents WebSocket stream_restarted events from interfering
            this.manualRestartInProgress.add(cameraId);
            console.log(`[Restart] ${cameraId}: Initiating backend FFmpeg restart...`);
            this.setStreamStatus($streamItem, 'loading', 'Restarting...');

            try {
                // Call backend restart endpoint
                const response = await fetch(`/api/stream/restart/${cameraId}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ type: 'sub' })
                });

                if (!response.ok) {
                    const error = await response.json().catch(() => ({}));
                    throw new Error(error.error || `HTTP ${response.status}`);
                }

                const result = await response.json();
                console.log(`[Restart] ${cameraId}: Backend restart successful, stream_url: ${result.stream_url}`);

                // Wait for FFmpeg to establish MediaMTX publish connection
                // FFmpeg needs ~2-3 seconds to connect and start publishing
                console.log(`[Restart] ${cameraId}: Waiting for FFmpeg to establish MediaMTX connection...`);
                await new Promise(r => setTimeout(r, 3000));

                // Reconnect to stream with retry logic
                // MediaMTX path may not be immediately available after FFmpeg starts
                const maxRetries = 3;
                const retryDelay = 2000;

                for (let attempt = 1; attempt <= maxRetries; attempt++) {
                    try {
                        console.log(`[Restart] ${cameraId}: Reconnecting (attempt ${attempt}/${maxRetries})...`);

                        if (streamType === 'HLS' || streamType === 'LL_HLS' || streamType === 'NEOLINK' || streamType === 'NEOLINK_LL_HLS') {
                            await this.hlsManager.forceRefreshStream(cameraId, videoElement);
                        } else if (streamType === 'WEBRTC') {
                            await this.webrtcManager.forceRefreshStream(cameraId, videoElement);
                        }

                        // Success - exit retry loop
                        break;
                    } catch (retryError) {
                        console.warn(`[Restart] ${cameraId}: Attempt ${attempt} failed: ${retryError.message}`);
                        if (attempt < maxRetries) {
                            console.log(`[Restart] ${cameraId}: Waiting ${retryDelay}ms before retry...`);
                            await new Promise(r => setTimeout(r, retryDelay));
                        } else {
                            throw retryError; // Final attempt failed, propagate error
                        }
                    }
                }

                this.setStreamStatus($streamItem, 'active', '');
                console.log(`[Restart] ${cameraId}: Stream fully restarted`);

            } catch (error) {
                console.error(`[Restart] ${cameraId}: Failed - ${error.message}`);
                this.setStreamStatus($streamItem, 'error', `Restart failed: ${error.message}`);
            } finally {
                // Clear manual restart flag regardless of success/failure
                this.manualRestartInProgress.delete(cameraId);
                console.log(`[Restart] ${cameraId}: Manual restart flow completed`);
            }
        });

        // ESC key to exit CSS fullscreen or expanded modal
        $(document).on('keydown', (e) => {
            if (e.key === 'Escape') {
                // First check fullscreen (higher priority)
                const $fullscreenItem = $('.stream-item.css-fullscreen');
                if ($fullscreenItem.length > 0) {
                    console.log('[Fullscreen] ESC key pressed, exiting fullscreen');
                    this.closeFullscreen();
                    return;
                }
                // Then check expanded modal
                const $expandedItem = $('.stream-item.expanded');
                if ($expandedItem.length > 0) {
                    console.log('[Expanded] ESC key pressed, collapsing modal');
                    this.collapseExpandedCamera();
                }
            }
        });

        // ============================================================================
        // EXPANDED MODAL MODE HANDLERS
        // Tap/click on camera card to expand to larger modal view
        // ============================================================================

        // Click on stream item (not buttons) to expand
        this.$container.on('click', '.stream-item', async (e) => {
            // Don't expand if clicking on a button or interactive element
            if ($(e.target).closest('button, .ptz-controls, .stream-controls, a, input, select').length) {
                return;
            }

            // Don't expand if already in fullscreen
            const $streamItem = $(e.currentTarget);
            if ($streamItem.hasClass('css-fullscreen')) {
                return;
            }

            // Toggle expanded state
            if ($streamItem.hasClass('expanded')) {
                this.collapseExpandedCamera();
            } else {
                await this.expandCamera($streamItem);
            }
        });

        // Click backdrop to collapse expanded camera
        $('#expanded-backdrop').on('click', () => {
            this.collapseExpandedCamera();
        });

        // Audio mute/unmute toggle handler
        this.$container.on('click', '.stream-audio-btn', (e) => {
            e.stopPropagation();
            const $button = $(e.currentTarget);
            const $streamItem = $button.closest('.stream-item');
            const $video = $streamItem.find('.stream-video');
            const videoEl = $video[0];
            const cameraId = $streamItem.data('camera-serial');

            if (!videoEl || videoEl.tagName !== 'VIDEO') {
                console.log('[Audio] Not a video element, cannot toggle audio');
                return;
            }

            // Toggle muted state
            videoEl.muted = !videoEl.muted;

            // Update button icon and state
            const $icon = $button.find('i');
            if (videoEl.muted) {
                $icon.removeClass('fa-volume-up').addClass('fa-volume-mute');
                $button.removeClass('audio-active');
                console.log(`[Audio] ${cameraId}: Muted`);
            } else {
                $icon.removeClass('fa-volume-mute').addClass('fa-volume-up');
                $button.addClass('audio-active');
                console.log(`[Audio] ${cameraId}: Unmuted`);
            }

            // Save preference to localStorage
            this.saveAudioPreference(cameraId, !videoEl.muted);
        });

        // PTZ controls toggle handler
        this.$container.on('click', '.stream-ptz-toggle-btn', (e) => {
            e.stopPropagation();
            const $button = $(e.currentTarget);
            const $streamItem = $button.closest('.stream-item');
            const $ptzControls = $streamItem.find('.ptz-controls');
            const cameraId = $streamItem.data('camera-serial');

            if (!$ptzControls.length) {
                console.log('[PTZ] No PTZ controls found for this camera');
                return;
            }

            // Toggle PTZ controls visibility
            const isVisible = $ptzControls.hasClass('ptz-visible');

            if (isVisible) {
                // Hide PTZ controls
                $ptzControls.removeClass('ptz-visible');
                $button.removeClass('ptz-active');
                console.log(`[PTZ] ${cameraId}: Controls hidden`);
            } else {
                // Show PTZ controls
                $ptzControls.addClass('ptz-visible');
                $button.addClass('ptz-active');
                console.log(`[PTZ] ${cameraId}: Controls shown`);
            }

            // Save preference to localStorage
            this.savePTZPreference(cameraId, !isVisible);
        });

        // Stream controls toggle handler (start/stop/refresh buttons)
        this.$container.on('click', '.stream-controls-toggle-btn', (e) => {
            e.stopPropagation();
            const $button = $(e.currentTarget);
            const $streamItem = $button.closest('.stream-item');
            const $streamControls = $streamItem.find('.stream-controls');
            const cameraId = $streamItem.data('camera-serial');

            if (!$streamControls.length) {
                console.log('[StreamControls] No stream controls found for this camera');
                return;
            }

            // Toggle stream controls visibility
            const isVisible = $streamControls.hasClass('stream-controls-visible');

            if (isVisible) {
                // Hide stream controls
                $streamControls.removeClass('stream-controls-visible');
                $button.removeClass('controls-active');
                console.log(`[StreamControls] ${cameraId}: Controls hidden`);
            } else {
                // Show stream controls
                $streamControls.addClass('stream-controls-visible');
                $button.addClass('controls-active');
                console.log(`[StreamControls] ${cameraId}: Controls shown`);
            }

            // Save preference to localStorage
            this.saveStreamControlsPreference(cameraId, !isVisible);
        });
    }

    /**
     * Save audio preference to localStorage
     */
    saveAudioPreference(cameraId, enabled) {
        try {
            const prefs = JSON.parse(localStorage.getItem('cameraAudioPreferences') || '{}');
            prefs[cameraId] = enabled;
            localStorage.setItem('cameraAudioPreferences', JSON.stringify(prefs));
        } catch (e) {
            console.warn('[Audio] Failed to save preference:', e);
        }
    }

    /**
     * Save PTZ visibility preference to localStorage
     */
    savePTZPreference(cameraId, visible) {
        try {
            const prefs = JSON.parse(localStorage.getItem('cameraPTZPreferences') || '{}');
            prefs[cameraId] = visible;
            localStorage.setItem('cameraPTZPreferences', JSON.stringify(prefs));
        } catch (e) {
            console.warn('[PTZ] Failed to save preference:', e);
        }
    }

    /**
     * Get PTZ visibility preference from localStorage
     */
    getPTZPreference(cameraId) {
        try {
            const prefs = JSON.parse(localStorage.getItem('cameraPTZPreferences') || '{}');
            return prefs[cameraId] || false;  // Default to hidden
        } catch (e) {
            return false;
        }
    }

    /**
     * Save stream controls visibility preference to localStorage
     */
    saveStreamControlsPreference(cameraId, visible) {
        try {
            const prefs = JSON.parse(localStorage.getItem('cameraStreamControlsPreferences') || '{}');
            prefs[cameraId] = visible;
            localStorage.setItem('cameraStreamControlsPreferences', JSON.stringify(prefs));
        } catch (e) {
            console.warn('[StreamControls] Failed to save preference:', e);
        }
    }

    /**
     * Get stream controls visibility preference from localStorage
     */
    getStreamControlsPreference(cameraId) {
        try {
            const prefs = JSON.parse(localStorage.getItem('cameraStreamControlsPreferences') || '{}');
            return prefs[cameraId] || false;  // Default to hidden
        } catch (e) {
            return false;
        }
    }

    /**
     * Get audio preference from localStorage
     */
    getAudioPreference(cameraId) {
        try {
            const prefs = JSON.parse(localStorage.getItem('cameraAudioPreferences') || '{}');
            return prefs[cameraId] || false;  // Default to muted
        } catch (e) {
            return false;
        }
    }

    /**
     * Apply saved PTZ visibility preference
     */
    applyPTZPreference(cameraId, $streamItem) {
        const visible = this.getPTZPreference(cameraId);
        const $ptzControls = $streamItem.find('.ptz-controls');
        const $ptzToggleBtn = $streamItem.find('.stream-ptz-toggle-btn');

        if (!$ptzControls.length || !$ptzToggleBtn.length) {
            return;  // No PTZ controls for this camera
        }

        if (visible) {
            $ptzControls.addClass('ptz-visible');
            $ptzToggleBtn.addClass('ptz-active');
            console.log(`[PTZ] ${cameraId}: Restored visible state from preferences`);
        } else {
            $ptzControls.removeClass('ptz-visible');
            $ptzToggleBtn.removeClass('ptz-active');
        }
    }

    /**
     * Apply saved stream controls visibility preference
     */
    applyStreamControlsPreference(cameraId, $streamItem) {
        const visible = this.getStreamControlsPreference(cameraId);
        const $streamControls = $streamItem.find('.stream-controls');
        const $toggleBtn = $streamItem.find('.stream-controls-toggle-btn');

        if (!$streamControls.length || !$toggleBtn.length) {
            return;  // No stream controls for this camera
        }

        if (visible) {
            $streamControls.addClass('stream-controls-visible');
            $toggleBtn.addClass('controls-active');
            console.log(`[StreamControls] ${cameraId}: Restored visible state from preferences`);
        } else {
            $streamControls.removeClass('stream-controls-visible');
            $toggleBtn.removeClass('controls-active');
        }
    }

    /**
     * Apply saved audio preference to a video element
     */
    applyAudioPreference(cameraId, $streamItem) {
        const enabled = this.getAudioPreference(cameraId);
        const $video = $streamItem.find('.stream-video');
        const $button = $streamItem.find('.stream-audio-btn');
        const videoEl = $video[0];

        if (!videoEl || videoEl.tagName !== 'VIDEO') return;

        // Apply preference (false = muted, true = unmuted)
        videoEl.muted = !enabled;

        // Update button state
        const $icon = $button.find('i');
        if (enabled) {
            $icon.removeClass('fa-volume-mute').addClass('fa-volume-up');
            $button.addClass('audio-active');
        } else {
            $icon.removeClass('fa-volume-up').addClass('fa-volume-mute');
            $button.removeClass('audio-active');
        }
    }

    /**
     * Pre-warm HLS streams for all mediaserver-based MJPEG cameras.
     * For portable devices using MJPEG, the mediaserver endpoint taps MediaMTX RTSP,
     * which requires the HLS FFmpeg to be publishing first. This fires off all HLS
     * start requests in parallel so streams are ready by the time MJPEG needs them.
     *
     * @returns {Promise<void>}
     */
    async preWarmHLSStreams() {
        const $streamItems = this.$container.find('.stream-item');

        // Only pre-warm if portable device (will use MJPEG)
        const urlParams = new URLSearchParams(window.location.search);
        const debugForceMJPEG = urlParams.get('forceMJPEG') === 'true';
        if (!isPortableDevice() && !debugForceMJPEG) {
            return;  // Desktop uses HLS/WebRTC directly, no pre-warm needed
        }

        // Identify cameras that will use mediaserver MJPEG (eufy, sv3c, neolink)
        // These need HLS running first. Native MJPEG cameras (reolink, unifi, amcrest) don't need pre-warm.
        const mediaserverCameras = [];

        $streamItems.each((_, item) => {
            const $item = $(item);
            const cameraId = $item.data('camera-serial');
            const cameraType = ($item.data('camera-type') || '').toLowerCase();
            const streamType = $item.data('stream-type') || '';

            // Skip cameras that have native MJPEG endpoints
            const hasNativeMJPEG = ['reolink', 'unifi', 'amcrest'].includes(cameraType);
            // NEOLINK cameras don't have native MJPEG even though they're Reolink
            const isNeolink = streamType === 'NEOLINK' || streamType === 'NEOLINK_LL_HLS';

            if (!hasNativeMJPEG || isNeolink) {
                mediaserverCameras.push(cameraId);
            }
        });

        if (mediaserverCameras.length === 0) {
            console.log('[PreWarm] No mediaserver cameras to pre-warm');
            return;
        }

        console.log(`[PreWarm] Checking ${mediaserverCameras.length} mediaserver cameras...`);

        // First, check which streams are already publishing (survives page reload)
        // This avoids unnecessary API calls and speeds up reload significantly
        const checkPromises = mediaserverCameras.map(async (cameraId) => {
            try {
                const resp = await fetch(`/hls/${cameraId}/index.m3u8`, {
                    method: 'HEAD',
                    cache: 'no-store'
                });
                return { cameraId, ready: resp.ok };
            } catch (e) {
                return { cameraId, ready: false };
            }
        });

        const checkResults = await Promise.all(checkPromises);
        const alreadyReady = checkResults.filter(r => r.ready).map(r => r.cameraId);
        const needsStart = checkResults.filter(r => !r.ready).map(r => r.cameraId);

        if (alreadyReady.length > 0) {
            console.log(`[PreWarm] ✓ ${alreadyReady.length} streams already publishing (reload detected)`);
        }

        if (needsStart.length === 0) {
            console.log('[PreWarm] All streams already ready - skipping HLS start');
            return;
        }

        console.log(`[PreWarm] Starting HLS for ${needsStart.length} cameras in parallel`);

        // Fire off HLS start requests only for cameras that need it
        const startPromises = needsStart.map(async (cameraId) => {
            try {
                const response = await fetch(`/api/stream/start/${cameraId}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ type: 'sub' })
                });
                if (response.ok) {
                    console.log(`[PreWarm] ✓ HLS started for ${cameraId}`);
                } else {
                    console.warn(`[PreWarm] HLS start returned ${response.status} for ${cameraId}`);
                }
            } catch (e) {
                console.warn(`[PreWarm] Failed to start HLS for ${cameraId}: ${e.message}`);
            }
        });

        // Wait for all HLS starts to complete (or fail) before proceeding
        await Promise.allSettled(startPromises);
        console.log(`[PreWarm] All HLS start requests completed`);

        // Poll until streams are actually publishing to MediaMTX
        // The API returns immediately but FFmpeg takes time to connect and publish
        console.log(`[PreWarm] Waiting for ${needsStart.length} streams to publish...`);
        const maxWaitMs = 15000;  // 15 seconds max
        const pollInterval = 500;
        const startTime = Date.now();

        while (Date.now() - startTime < maxWaitMs) {
            // Check which streams are now ready
            const pollPromises = needsStart.map(async (cameraId) => {
                try {
                    const resp = await fetch(`/hls/${cameraId}/index.m3u8`, {
                        method: 'HEAD',
                        cache: 'no-store'
                    });
                    return { cameraId, ready: resp.ok };
                } catch (e) {
                    return { cameraId, ready: false };
                }
            });

            const results = await Promise.all(pollPromises);
            const readyCount = results.filter(r => r.ready).length;
            const notReady = results.filter(r => !r.ready).map(r => r.cameraId);

            if (notReady.length === 0) {
                const elapsed = Date.now() - startTime;
                console.log(`[PreWarm] ✓ All ${needsStart.length} streams ready after ${elapsed}ms`);
                break;
            }

            // Log progress every few polls
            if ((Date.now() - startTime) % 2000 < pollInterval) {
                console.log(`[PreWarm] ${readyCount}/${needsStart.length} streams ready, waiting for: ${notReady.slice(0, 3).join(', ')}${notReady.length > 3 ? '...' : ''}`);
            }

            await new Promise(r => setTimeout(r, pollInterval));
        }

        console.log('[PreWarm] Pre-warm complete, proceeding with MJPEG loading');
    }

    /**
     * Start all MJPEG streams using WebSocket multiplexing.
     *
     * Instead of opening one HTTP connection per camera (limited to ~6 by browser),
     * uses a single WebSocket connection to receive all camera frames.
     *
     * Steps:
     * 1. Connect to WebSocket /mjpeg namespace
     * 2. Prepare elements (swap video->img if needed)
     * 3. Subscribe to all camera streams
     * 4. Server starts sending multiplexed frames
     *
     * @returns {Promise<void>}
     */
    async startWebSocketMJPEGStreams() {
        const $streamItems = this.$container.find('.stream-item');
        console.log(`[WS-MJPEG] Starting WebSocket MJPEG for ${$streamItems.length} cameras`);

        // Step 1: Connect to WebSocket server
        try {
            await this.wsMjpegManager.connect();
            console.log('[WS-MJPEG] Connected to server');
        } catch (error) {
            console.error('[WS-MJPEG] Connection failed, falling back to HTTP MJPEG:', error);
            // Fall back to sequential HTTP MJPEG
            return this.startAllStreamsSequential();
        }

        // Step 2: Prepare elements and collect camera IDs
        const cameraIds = [];
        const elementMap = new Map();

        $streamItems.each((_, item) => {
            const $item = $(item);
            const cameraId = $item.data('camera-serial');
            const streamType = $item.data('stream-type');

            // Store original stream type for fullscreen switching
            $item.data('original-stream-type', streamType);
            $item.data('stream-type', 'MJPEG');

            // Swap video->img if needed for MJPEG display
            let streamElement = $item.find('.stream-video')[0];
            if (streamElement && streamElement.tagName === 'VIDEO') {
                console.log(`[WS-MJPEG] ${cameraId}: Swapping <video> for <img>`);
                const $video = $(streamElement);

                // Create <img> element
                const $img = $('<img>', {
                    class: 'stream-video stream-mjpeg-img stream-ws-mjpeg',
                    style: 'object-fit: cover; width: 100%; height: 100%;',
                    alt: 'WebSocket MJPEG Stream'
                });

                $video.before($img);
                $video.hide();
                streamElement = $img[0];
                $item.data('mjpeg-swapped', true);
            }

            cameraIds.push(cameraId);
            elementMap.set(cameraId, streamElement);

            // Update UI
            this.setStreamStatus($item, 'loading', 'Connecting...');
        });

        // Step 3: Subscribe to camera streams
        const success = this.wsMjpegManager.subscribe(cameraIds, elementMap);

        if (success) {
            // Update all stream statuses to live
            $streamItems.each((_, item) => {
                const $item = $(item);
                this.setStreamStatus($item, 'live', 'Live (WS)');
                this.updateStreamButtons($item, true);
            });

            console.log(`[WS-MJPEG] Subscribed to ${cameraIds.length} cameras`);
        } else {
            console.error('[WS-MJPEG] Subscription failed');
            $streamItems.each((_, item) => {
                this.setStreamStatus($(item), 'error', 'WS Subscribe failed');
            });
        }
    }

    /**
     * Fallback sequential MJPEG loading when WebSocket is unavailable.
     * Called when WebSocket connection fails.
     */
    async startAllStreamsSequential() {
        const $streamItems = this.$container.find('.stream-item');
        console.log(`[StartAll] Fallback: Sequential MJPEG for ${$streamItems.length} cameras`);

        for (let index = 0; index < $streamItems.length; index++) {
            const $item = $($streamItems[index]);
            const cameraId = $item.data('camera-serial');
            const cameraType = $item.data('camera-type');
            const streamType = $item.data('stream-type');

            try {
                await this.startStream(cameraId, $item, cameraType, streamType);
            } catch (error) {
                console.error(`[StartAll] Failed: ${cameraId}`, error);
                this.setStreamStatus($item, 'error', 'Failed to load');
            }

            if (index < $streamItems.length - 1) {
                await new Promise(r => setTimeout(r, 300));
            }
        }
    }

    /**
     * Start all streams with sequential loading to prevent resource exhaustion.
     * Uses a delay between stream starts to allow each video element to initialize
     * before the next one starts. This is especially important for:
     * - iOS Safari (hard limit on simultaneous video decodes ~4-8)
     * - Mobile devices with limited resources
     * - Systems with many cameras (11+)
     *
     * WebSocket MJPEG mode (?useWebSocketMJPEG=true):
     * - All MJPEG cameras use a single WebSocket connection
     * - Bypasses browser's ~6 HTTP connection limit
     * - Frames are multiplexed and demultiplexed by camera ID
     *
     * @param {number} delayMs - Delay between stream starts in milliseconds (default: 300)
     */
    async startAllStreams(delayMs = 300) {
        console.log('[StartAll] Beginning startAllStreams (sequential loading)...');

        // Pre-warm HLS streams for portable devices before starting MJPEG connections
        // This fires off all HLS start requests in parallel so MediaMTX has the streams
        // ready by the time each MJPEG connection needs to tap into them.
        await this.preWarmHLSStreams();

        // WebSocket MJPEG mode: use single multiplexed connection for all MJPEG streams
        // This bypasses browser's ~6 HTTP connection limit for much faster loading
        const urlParams = new URLSearchParams(window.location.search);
        const debugForceMJPEG = urlParams.get('forceMJPEG') === 'true';
        const shouldUseMJPEG = isPortableDevice() || debugForceMJPEG;

        if (this.useWebSocketMJPEG && shouldUseMJPEG) {
            console.log('[StartAll] Using WebSocket MJPEG for multiplexed streaming');
            await this.startWebSocketMJPEGStreams();
            console.log('[StartAll] ✓✓✓ WEBSOCKET MJPEG COMPLETE ✓✓✓');
            return;
        }

        // iOS pagination: only start streams on current page (others are hidden)
        // This avoids overwhelming Safari's video decode limits
        if (this.iosPagination.enabled) {
            console.log('[StartAll] iOS pagination enabled - starting only current page streams');
            await this.startCurrentPageStreams();
            console.log('[StartAll] ✓✓✓ iOS PAGE STREAMS COMPLETE ✓✓✓');
            return;
        }

        // Non-iOS: start all streams sequentially
        const $streamItems = this.$container.find('.stream-item');
        console.log(`[StartAll] Found ${$streamItems.length} stream items, delay=${delayMs}ms`);

        // Sequential loading with delays to prevent resource exhaustion
        // This applies to all UIs (desktop, mobile, iOS) for consistent behavior
        for (let index = 0; index < $streamItems.length; index++) {
            const $item = $($streamItems[index]);
            const cameraId = $item.data('camera-serial');
            const cameraType = $item.data('camera-type');
            const streamType = $item.data('stream-type');

            console.log(`[StartAll] Starting stream ${index + 1}/${$streamItems.length}: ${cameraId}`);

            try {
                await this.startStream(cameraId, $item, cameraType, streamType);
                console.log(`[StartAll] ✓ Stream ${index + 1} started: ${cameraId}`);
            } catch (error) {
                console.error(`[StartAll] ✗ Stream ${index + 1} failed: ${cameraId}`, error);
                this.setStreamStatus($item, 'error', 'Failed to load');
            }

            // Delay between stream starts to let browser initialize each video element
            // Skip delay after last stream
            if (index < $streamItems.length - 1) {
                await new Promise(r => setTimeout(r, delayMs));
            }
        }

        console.log('[StartAll] ✓✓✓ ALL STREAMS COMPLETE ✓✓✓');
    }

    async startStream(cameraId, $streamItem, cameraType, streamType) {
        let streamElement = $streamItem.find('.stream-video')[0];
        const $loadingIndicator = $streamItem.find('.loading-indicator');

        // Portable device MJPEG override for grid view
        // iOS/mobile devices have limited video decode resources. MJPEG uses <img> tags
        // which are much lighter than <video> elements and bypass Safari's decode limits.
        // This only applies to grid view (not fullscreen where we want HLS for audio support).
        //
        // iOS GRID MODE: Use snapshot polling (1 JPEG/sec) instead of MJPEG streams.
        // MJPEG multipart streams are unreliable on iOS Safari.
        // Snapshots are lightweight, load instantly, and work reliably.
        //
        // ANDROID/OTHER PORTABLE: Still use MJPEG (works fine on non-iOS)
        //
        // DEBUG: Add ?forceMJPEG=true to URL to test MJPEG on desktop
        const urlParams = new URLSearchParams(window.location.search);
        const debugForceMJPEG = urlParams.get('forceMJPEG') === 'true';
        const debugForceSnapshot = urlParams.get('forceSnapshot') === 'true';
        const isGridView = !$streamItem.hasClass('css-fullscreen');

        // iOS in grid view: use snapshots (not MJPEG) unless user forces WebRTC
        // forceWebRTCGrid is experimental - may cause issues with many cameras
        const useIOSSnapshot = isIOSDevice() && isGridView && !debugForceMJPEG && !isForceWebRTCGridEnabled();
        // Desktop users can opt-in to snapshot mode via Settings
        const useDesktopSnapshot = !isPortableDevice() && isGridSnapshotsEnabled() && isGridView;
        // Android/other portable in grid: use MJPEG
        const forcePortableMJPEG = (isPortableDevice() && !isIOSDevice() || debugForceMJPEG) && isGridView;
        // Use snapshots for: iOS, desktop opt-in, or debug mode
        const useSnapshot = useIOSSnapshot || useDesktopSnapshot || (debugForceSnapshot && isGridView);

        if (useSnapshot && streamType !== 'SNAPSHOT') {
            const snapshotReason = isIOSDevice() ? 'iOS' : (useDesktopSnapshot ? 'Desktop setting' : 'Debug');
            console.log(`[Stream] ${snapshotReason} grid mode - using snapshot polling for ${cameraId}`);
            streamType = 'SNAPSHOT';
            // Store original stream type for fullscreen switching
            $streamItem.data('original-stream-type', $streamItem.data('stream-type'));
            $streamItem.data('stream-type', 'SNAPSHOT');

            // Snapshots require an <img> element
            if (streamElement && streamElement.tagName === 'VIDEO') {
                console.log(`[Stream] ${cameraId}: Swapping <video> for <img> element for snapshots`);
                const $video = $(streamElement);

                const $img = $('<img>', {
                    class: 'stream-video stream-snapshot-img',
                    style: 'object-fit: cover; width: 100%; height: 100%;',
                    alt: 'Snapshot Stream'
                });

                $video.before($img);
                $video.hide();
                streamElement = $img[0];
                $streamItem.data('snapshot-swapped', true);
            }
        } else if (forcePortableMJPEG && streamType !== 'MJPEG' && streamType !== 'mjpeg_proxy' && streamType !== 'SNAPSHOT') {
            console.log(`[Stream] Portable device detected - using MJPEG for ${cameraId} grid view`);
            streamType = 'MJPEG';  // Override to MJPEG for lighter resource usage
            // Store original stream type for fullscreen switching
            $streamItem.data('original-stream-type', $streamItem.data('stream-type'));
            $streamItem.data('stream-type', 'MJPEG');

            // CRITICAL: MJPEG requires an <img> element, not <video>
            if (streamElement && streamElement.tagName === 'VIDEO') {
                console.log(`[Stream] ${cameraId}: Swapping <video> for <img> element for MJPEG`);
                const $video = $(streamElement);

                const $img = $('<img>', {
                    class: 'stream-video stream-mjpeg-img',
                    style: 'object-fit: cover; width: 100%; height: 100%;',
                    alt: 'MJPEG Stream'
                });

                $video.before($img);
                $video.hide();
                streamElement = $img[0];
                $streamItem.data('mjpeg-swapped', true);
            }
        }

        // iOS Force WebRTC Grid Mode (experimental)
        // When enabled, iOS grid view uses WebRTC instead of snapshot polling
        // This provides real-time video but may cause issues with many cameras
        const forceIOSWebRTCGrid = isIOSDevice() && isGridView && isForceWebRTCGridEnabled();
        if (forceIOSWebRTCGrid && streamType !== 'WEBRTC') {
            console.log(`[Stream] iOS Force WebRTC Grid enabled - using WebRTC for ${cameraId}`);
            // Store original stream type for recovery
            $streamItem.data('original-stream-type', $streamItem.data('stream-type'));
            streamType = 'WEBRTC';
            $streamItem.data('stream-type', 'WEBRTC');
        }

        try {
            // Clear user-stopped flag when stream is being started (user wants it running)
            this.clearUserStoppedStream(cameraId);

            $loadingIndicator.show();
            this.setStreamStatus($streamItem, 'loading', 'Starting...');

            // Timeout for stuck "Starting..." state
            // If stream doesn't become live within 15 seconds, trigger reconnection via health monitor
            const startupTimeout = setTimeout(() => {
                const $indicator = $streamItem.find('.stream-indicator');
                if ($indicator.hasClass('loading')) {
                    console.log(`[Stream] ${cameraId}: Startup timeout - no media in 15s, triggering health monitor`);
                    this.setStreamStatus($streamItem, 'loading', 'Reconnecting...');
                    // Dispatch error event so health monitor can retry
                    const el = $streamItem.find('.stream-video')[0];
                    if (el) {
                        el.dispatchEvent(new CustomEvent('streamerror', {
                            detail: { cameraId, error: 'Startup timeout' }
                        }));
                    }
                }
            }, 15000);
            $streamItem.data('startup-timeout', startupTimeout);

            let success;

            // Use streamType to determine which manager to use
            // NOTE: mjpeg_proxy is only for direct access to UNIFI MJPEG streams (when not using Protect)
            if (streamType === 'SNAPSHOT') {
                // Snapshot polling - lightweight, reliable on iOS Safari
                // Polls /api/snap/<camera_id> every 1 second for single JPEGs
                success = await this.snapshotManager.startStream(cameraId, streamElement, cameraType, 1000);
            } else if (streamType === 'MJPEG' || streamType === 'mjpeg_proxy') {
                // Pass 'sub' as stream parameter for grid view (Reolink requires this for MJPEG endpoint)
                success = await this.mjpegManager.startStream(cameraId, streamElement, cameraType, 'sub');
            } else if (streamType === 'HLS' || streamType === 'LL_HLS' || streamType === 'NEOLINK' || streamType === 'NEOLINK_LL_HLS') {
                success = await this.hlsManager.startStream(cameraId, streamElement, 'sub');
            } else if (streamType === 'RTMP') {
                success = await this.flvManager.startStream(cameraId, streamElement);
            } else if (streamType === 'WEBRTC') {
                // WebRTC via MediaMTX WHEP protocol - sub-second latency (~200ms)
                // iOS Safari requires encrypted WebRTC (DTLS-SRTP).
                // Check if DTLS is enabled on the server before attempting WebRTC on iOS.
                if (isIOSDevice()) {
                    const dtlsEnabled = await isDTLSEnabled();
                    if (dtlsEnabled) {
                        // DTLS enabled - iOS can use WebRTC for low latency
                        console.log(`[Stream] iOS + DTLS enabled - using WebRTC for ${cameraId} (~200ms latency)`);
                        success = await this.webrtcManager.startStream(cameraId, streamElement, 'sub');
                    } else {
                        // No DTLS - fall back to HLS (iOS requires encryption)
                        console.log(`[Stream] iOS without DTLS - falling back to HLS for ${cameraId} (~2-4s latency)`);
                        success = await this.hlsManager.startStream(cameraId, streamElement, 'sub');
                        // Update the stream type on the element so fullscreen/recovery works correctly
                        $streamItem.data('stream-type', 'LL_HLS');
                    }
                } else {
                    // Non-iOS: WebRTC works without DTLS on LAN
                    success = await this.webrtcManager.startStream(cameraId, streamElement, 'sub');
                }
            } else {
                throw new Error(`Unknown stream type: ${streamType}`);
            }

            if (success) {
                // Clear startup timeout - stream is now live
                const startupTimeout = $streamItem.data('startup-timeout');
                if (startupTimeout) {
                    clearTimeout(startupTimeout);
                    $streamItem.removeData('startup-timeout');
                }

                $loadingIndicator.hide();
                this.setStreamStatus($streamItem, 'live', 'Live');
                this.updateStreamButtons($streamItem, true);

                // Reset restart counter on successful start
                this.restartAttempts.delete(cameraId);
                if (this.restartTimers.has(cameraId)) {
                    clearTimeout(this.restartTimers.get(cameraId));
                    this.restartTimers.delete(cameraId);
                }

                // Apply saved audio preference (unmute if user previously enabled)
                this.applyAudioPreference(cameraId, $streamItem);

                // Apply saved PTZ visibility preference
                this.applyPTZPreference(cameraId, $streamItem);

                // Apply saved stream controls visibility preference
                this.applyStreamControlsPreference(cameraId, $streamItem);

                // Attach health monitor (refactored)
                this.attachHealthMonitor(cameraId, $streamItem, streamType);
            }
        } catch (error) {
            $loadingIndicator.hide();
            console.error(`Stream start failed for ${cameraId}:`, error);

            // For HLS/LL_HLS/WEBRTC streams, show "Connecting..." instead of "Failed"
            // These streams have retry logic and may still succeed
            const isRetryableStream = ['HLS', 'LL_HLS', 'NEOLINK', 'NEOLINK_LL_HLS', 'WEBRTC'].includes(streamType);
            if (isRetryableStream) {
                this.setStreamStatus($streamItem, 'loading', 'Connecting...');
                this.updateStreamButtons($streamItem, true);  // Keep buttons enabled for retry
            } else {
                this.setStreamStatus($streamItem, 'error', 'Failed');
                this.updateStreamButtons($streamItem, false);
            }

            // CRITICAL: Attach health monitor even on initial failure so it can retry
            this.attachHealthMonitor(cameraId, $streamItem, streamType);
        }
    }

    /**
     * Stop an individual stream.
     *
     * @param {string} cameraId - Camera serial number
     * @param {jQuery} $streamItem - Stream item element
     * @param {string} cameraType - Camera vendor type
     * @param {string} streamType - Stream type (HLS, WEBRTC, MJPEG, etc.)
     * @param {Object} options - Optional configuration
     * @param {boolean} options.userInitiated - Set to true ONLY when user clicks stop button.
     *        This marks the stream as "user-stopped" in localStorage, preventing auto-restart.
     *        DO NOT set this for programmatic stops (recovery, page switches, etc.)!
     */
    async stopIndividualStream(cameraId, $streamItem, cameraType, streamType, options = {}) {
        try {
            // Track user-initiated stops to prevent watchdog/health monitor from auto-restarting
            // When userInitiated=true, we record this in localStorage so recovery logic skips this stream
            if (options.userInitiated) {
                this.markStreamAsUserStopped(cameraId);
                console.log(`[Stream] ${cameraId}: User-initiated stop - marking as user-stopped`);
            }

            // Clear any pending startup timeout
            const startupTimeout = $streamItem.data('startup-timeout');
            if (startupTimeout) {
                clearTimeout(startupTimeout);
                $streamItem.removeData('startup-timeout');
            }

            let success;

            // Use streamType to determine which manager to use
            if (streamType === 'SNAPSHOT') {
                success = this.snapshotManager.stopStream(cameraId);
            } else if (streamType === 'MJPEG' || streamType === 'mjpeg_proxy') {
                success = this.mjpegManager.stopStream(cameraId);
            } else if (streamType === 'HLS' || streamType === 'LL_HLS' || streamType === 'NEOLINK' || streamType === 'NEOLINK_LL_HLS') {
                success = await this.hlsManager.stopStream(cameraId);
            } else if (streamType === 'RTMP') {
                success = this.flvManager.stopStream(cameraId);
            } else if (streamType === 'WEBRTC') {
                success = this.webrtcManager.stopStream(cameraId);
            }

            if (success) {
                const $streamElement = $streamItem.find('.stream-video');
                $streamElement.attr('src', '');
                this.setStreamStatus($streamItem, 'loading', 'Stopped');
                this.updateStreamButtons($streamItem, false);
                const el = $streamItem.find('.stream-video')[0];
                if (el && el._healthDetach) { el._healthDetach(); delete el._healthDetach; }

            }
        } catch (error) {
            console.error(`Failed to stop stream for ${cameraId}:`, error);
        }
    }

    async stopAllStreams() {
        try {
            // Stop all manager types in parallel (including snapshot polling)
            const stopPromises = [
                this.mjpegManager.stopAllStreams(),
                this.hlsManager.stopAllStreams(),
                this.flvManager.stopAllStreams(),
                this.webrtcManager.stopAllStreams(),
                this.snapshotManager.stopAllStreams()
            ];

            await Promise.allSettled(stopPromises);

            // Update UI for all streams
            const $streamItems = this.$container.find('.stream-item');
            $streamItems.each((index, item) => {
                const $item = $(item);
                const $streamElement = $item.find('.stream-video');
                $streamElement.attr('src', '');
                this.setStreamStatus($item, 'loading', 'Stopped');
                this.updateStreamButtons($item, false);

            });
            // also detach any existing monitors:
            this.$container.find('.stream-video').each((_, v) => {
                if (v._healthDetach) { v._healthDetach(); delete v._healthDetach; }
            });

        } catch (error) {
            console.error('Failed to stop all streams:', error);
        }
    }

    /**
     * Restart a stream that has become unhealthy or frozen
     * 
     * This method is typically called by the health monitor when a stream is detected
     * as stale (no new frames) or displaying a black screen. It handles the complete
     * restart lifecycle:
     * 
     * 1. Detaches health monitor to prevent duplicate monitoring during restart
     * 2. Dispatches to stream-type-specific restart method (HLS/MJPEG/RTMP)
     * 3. Updates UI status to 'live' on success
     * 4. Reattaches health monitor (whether success or failure)
     * 
     * The health monitor is ALWAYS reattached after restart (success or failure) to
     * ensure continuous monitoring and automatic retry attempts. Without reattachment,
     * a failed restart would leave the stream permanently stuck with no recovery path.
     * 
     * @param {string} cameraId - Camera serial number (e.g., 'T8416P0023352DA9')
     * @param {jQuery} $streamItem - jQuery-wrapped DOM element for the stream container
     * 
     * @throws {Error} Caught internally - errors logged but health monitor reattached
     * 
     * @example
     * // Called automatically by health monitor
     * await this.restartStream('REOLINK_LAUNDRY', $('.stream-item[data-camera-serial="REOLINK_LAUNDRY"]'));
     */
    async restartStream(cameraId, $streamItem) {
        try {
            console.log(`[Restart] ${cameraId}: Beginning restart sequence`);
            this.updateStreamButtons($streamItem, true);
            this.setStreamStatus($streamItem, 'loading', 'Restarting...');

            const cameraType = $streamItem.data('camera-type');
            const streamType = $streamItem.data('stream-type');
            const videoElement = $streamItem.find('.stream-video')[0];

            // Detach health monitor during restart
            if (videoElement && videoElement._healthDetach) {
                videoElement._healthDetach();
                delete videoElement._healthDetach;
            }

            // Dispatch to appropriate restart method
            if (streamType === 'HLS' || streamType === 'LL_HLS' || streamType === 'NEOLINK' || streamType === 'NEOLINK_LL_HLS') {
                await this.restartHLSStream(cameraId, videoElement);
            } else if (streamType === 'MJPEG' || streamType === 'mjpeg_proxy') {
                await this.restartMJPEGStream(cameraId, $streamItem, cameraType, streamType);
            } else if (streamType === 'RTMP') {
                await this.restartRTMPStream(cameraId, $streamItem, cameraType, streamType);
            } else if (streamType === 'WEBRTC') {
                await this.restartWebRTCStream(cameraId, videoElement);
            }



            // Success: update status and reattach health
            this.setStreamStatus($streamItem, 'live', 'Live');
            this.attachHealthMonitor(cameraId, $streamItem, streamType);

            console.log(`[Restart] ${cameraId}: Restart complete`);

        } catch (e) {
            console.error(`[Restart] ${cameraId}: Failed`, e);
            this.setStreamStatus($streamItem, 'error', 'Restart failed');
            // const cameraType = $streamItem.data('camera-type');
            // const streamType = $streamItem.data('stream-type');
            // await this.nuclear(cameraId, streamItem, cameraType, streamType)

            // Reattach health even on failure so it can retry
            this.attachHealthMonitor(cameraId, $streamItem, streamType);
        }
    }

    /**
     * Handle backend recovery notification from CameraStateMonitor.
     * Called when StreamWatchdog has successfully restarted a stream.
     *
     * Performs full stop+start cycle to ensure clean reconnection:
     * - HLS.js refresh alone may stay connected to stale MediaMTX session
     * - Full stop+start forces fresh connection to new backend publisher
     *
     * @param {string} cameraId - Camera serial number
     * @param {jQuery} $streamItem - Stream item element
     */
    async handleBackendRecovery(cameraId, $streamItem) {
        try {
            // CRITICAL: Skip recovery for user-stopped streams
            // User explicitly stopped this stream via UI, don't auto-restart
            if (this.isUserStoppedStream(cameraId)) {
                console.log(`[Recovery] ${cameraId}: Skipping backend recovery - user manually stopped this stream`);
                return;
            }

            // Clear any pending UI health restart timers (backend already fixed it)
            if (this.restartTimers.has(cameraId)) {
                clearTimeout(this.restartTimers.get(cameraId));
                this.restartTimers.delete(cameraId);
                console.log(`[Recovery] ${cameraId}: Cleared pending UI health restart timer`);
            }

            // Reset restart attempt counter since backend fixed it
            this.restartAttempts.delete(cameraId);
            this.recentFailures.delete(cameraId);

            // Get stream metadata
            const streamType = $streamItem.data('stream-type');
            const videoElement = $streamItem.find('.stream-video')[0];

            console.log(`[Recovery] ${cameraId}: streamType='${streamType}', videoElement exists: ${!!videoElement}`);

            // For HLS and WebRTC: use forceRefreshStream() - same as manual refresh button
            // This is faster and more reliable than full stop+start cycle
            if (streamType === 'HLS' || streamType === 'LL_HLS' || streamType === 'NEOLINK' || streamType === 'NEOLINK_LL_HLS') {
                console.log(`[Recovery] ${cameraId}: Using HLS forceRefreshStream (same as manual refresh)`);
                this.setStreamStatus($streamItem, 'loading', 'Refreshing HLS...');
                await new Promise(r => setTimeout(r, 1000)); // Brief delay for backend to stabilize
                this.hlsManager.forceRefreshStream(cameraId, videoElement);
                console.log(`[Recovery] ${cameraId}: HLS refresh triggered`);
            } else if (streamType === 'WEBRTC') {
                // IMPORTANT: For WebRTC, call forceRefreshStream IMMEDIATELY without delay
                // The 1-second delay was causing T8416P0023352DA9 to fail while manual refresh worked
                // Manual refresh button calls forceRefreshStream directly - we should too
                console.log(`[Recovery] ${cameraId}: Using WebRTC forceRefreshStream (same as manual refresh - NO delay)`);
                this.setStreamStatus($streamItem, 'loading', 'Refreshing WebRTC...');
                this.webrtcManager.forceRefreshStream(cameraId, videoElement);
                console.log(`[Recovery] ${cameraId}: WebRTC refresh triggered`);

                // FALLBACK: If video is still black after 5 seconds, trigger refresh button click directly
                // This is the "nuclear option" - directly clicking the button that we know works
                setTimeout(() => {
                    if (videoElement && (videoElement.readyState < 2 || videoElement.videoWidth === 0)) {
                        console.log(`[Recovery] ${cameraId}: WebRTC still black after 5s - triggering refresh button click directly`);
                        const $refreshBtn = $streamItem.find('.refresh-stream-btn');
                        if ($refreshBtn.length) {
                            $refreshBtn.trigger('click');
                        }
                    } else {
                        console.log(`[Recovery] ${cameraId}: WebRTC recovered successfully (readyState=${videoElement?.readyState}, videoWidth=${videoElement?.videoWidth})`);
                    }
                }, 5000);
            } else {
                // For other stream types: full stop+start cycle
                console.log(`[Recovery] ${cameraId}: Using full stop+start cycle for ${streamType}`);
                this.setStreamStatus($streamItem, 'loading', 'Recovered - Reconnecting...');
                await new Promise(r => setTimeout(r, 2000));

                const cameraType = $streamItem.data('camera-type');
                await this.stopIndividualStream(cameraId, $streamItem, cameraType, streamType);
                await new Promise(r => setTimeout(r, 500));
                await this.startStream(cameraId, $streamItem, cameraType, streamType);
                console.log(`[Recovery] ${cameraId}: Full stop+start complete`);
            }

        } catch (e) {
            console.error(`[Recovery] ${cameraId}: Failed to reconnect after backend recovery`, e);
            this.setStreamStatus($streamItem, 'error', 'Recovery reconnect failed');
        }
    }

    /**
     * Restart HLS/LL-HLS stream by destroying and recreating HLS.js instance
     */
    async restartHLSStream(cameraId, videoElement) {
        await this.hlsManager.forceRefreshStream(cameraId, videoElement);
    }

    /**
     * Restart MJPEG stream by stopping and restarting
     */
    async restartMJPEGStream(cameraId, $streamItem, cameraType, streamType) {
        await this.stopIndividualStream(cameraId, $streamItem, cameraType, streamType);
        await new Promise(r => setTimeout(r, 1500));
        await this.startStream(cameraId, $streamItem, cameraType, streamType);
    }

    /**
     * Restart RTMP stream by destroying and recreating FLV player
     */
    async restartRTMPStream(cameraId, $streamItem, cameraType, streamType) {
        this.flvManager.stopStream(cameraId);
        await new Promise(r => setTimeout(r, 500));
        const success = await this.startStream(cameraId, $streamItem, cameraType, streamType);

        // Give element time to start playing
        await new Promise(r => setTimeout(r, 800));

        const el = $streamItem.find('.stream-video')[0];
        if (success && el && el.readyState >= 2 && !el.paused) {
            return true;
        } else if (success) {
            this.setStreamStatus($streamItem, 'loading', 'Buffering…');
            return false;
        }
        return false;
    }

    /**
     * Restart WebRTC stream by closing and reopening RTCPeerConnection
     */
    async restartWebRTCStream(cameraId, videoElement) {
        await this.webrtcManager.forceRefreshStream(cameraId, videoElement);
    }

    /**
     * Attach health monitor to a stream element
     * Centralizes health attachment logic to avoid repetition
     */
    attachHealthMonitor(cameraId, $streamItem, streamType) {
        if (!this.health) {
            console.log(`[Health] Monitoring disabled for ${cameraId}`);
            return;
        }

        const el = $streamItem.find('.stream-video')[0];
        if (!el) {
            console.warn(`[Health] No video element found for ${cameraId}`);
            return;
        }

        console.log(`[Health] Attaching monitor for ${cameraId} (${streamType})`);

        // Listen for HLS stream events to update UI status
        if (!el._streamEventsBound) {
            el._streamEventsBound = true;

            // Update UI when stream goes live (first fragment received)
            el.addEventListener('streamlive', () => {
                console.log(`[Stream] ${cameraId}: Received 'streamlive' event`);
                this.setStreamStatus($streamItem, 'live', 'Live');
                this.restartAttempts.delete(cameraId);
            });

            // Update UI during retry attempts
            el.addEventListener('streamretrying', (e) => {
                const { retry, maxRetries } = e.detail;
                console.log(`[Stream] ${cameraId}: Received 'streamretrying' event (${retry}/${maxRetries})`);
                this.setStreamStatus($streamItem, 'loading', `Retry ${retry}/${maxRetries}...`);
            });
        }

        if (streamType === 'HLS' || streamType === 'LL_HLS' || streamType === 'NEOLINK' || streamType === 'NEOLINK_LL_HLS') {
            const hls = this.hlsManager?.hlsInstances?.get?.(cameraId) || null;
            el._healthDetach = this.health.attachHls(cameraId, el, hls);
        } else if (streamType === 'RTMP') {
            const flv = this.flvManager?.flvInstances?.get?.(cameraId) || null;
            el._healthDetach = this.health.attachRTMP(cameraId, el, flv);
        } else if (streamType === 'MJPEG' || streamType === 'mjpeg_proxy') {
            el._healthDetach = this.health.attachMjpeg(cameraId, el);
        } else if (streamType === 'WEBRTC') {
            const pc = this.webrtcManager?.activeStreams?.get?.(cameraId)?.pc || null;
            el._healthDetach = this.health.attachWebRTC(cameraId, el, pc);
        }
    }


    updateStreamButtons($streamItem, isStreaming) {
        const $startBtn = $streamItem.find('.start-stream-btn');
        const $stopBtn = $streamItem.find('.stop-stream-btn');
        const $ptzBtns = $streamItem.find('.ptz-btn');

        $startBtn.prop('disabled', isStreaming);
        $stopBtn.prop('disabled', !isStreaming);

        // PTZ works regardless of streaming
        $ptzBtns.prop('disabled', false);
    }

    // ============================================================================
    // User-Stopped Stream Tracking
    // ============================================================================
    // When user explicitly stops a stream via UI button, we track this in localStorage.
    // Recovery logic (watchdog/health monitor) checks this list and skips auto-restarting
    // streams the user manually stopped. This prevents the annoying behavior where
    // the system keeps restarting streams the user intentionally stopped.
    // ============================================================================

    /**
     * Get the Set of camera IDs that user has manually stopped.
     * Stored in localStorage as JSON array for persistence across page refreshes.
     * @returns {Set<string>} Set of user-stopped camera IDs
     */
    _getUserStoppedStreams() {
        try {
            const stored = localStorage.getItem('userStoppedStreams');
            return stored ? new Set(JSON.parse(stored)) : new Set();
        } catch (e) {
            console.error('[UserStopped] Error reading localStorage:', e);
            return new Set();
        }
    }

    /**
     * Persist the Set of user-stopped camera IDs to localStorage.
     * @param {Set<string>} stoppedSet - Set of camera IDs to persist
     */
    _saveUserStoppedStreams(stoppedSet) {
        try {
            localStorage.setItem('userStoppedStreams', JSON.stringify([...stoppedSet]));
        } catch (e) {
            console.error('[UserStopped] Error saving to localStorage:', e);
        }
    }

    /**
     * Mark a camera as user-stopped (should not be auto-restarted).
     * Called when user clicks stop button on a stream.
     * @param {string} cameraId - Camera serial number
     */
    markStreamAsUserStopped(cameraId) {
        const stopped = this._getUserStoppedStreams();
        stopped.add(cameraId);
        this._saveUserStoppedStreams(stopped);
        console.log(`[UserStopped] Marked ${cameraId} as user-stopped. Total: ${stopped.size}`);
    }

    /**
     * Clear user-stopped flag for a camera (allow auto-restart).
     * Called when user starts a stream (indicates they want it running).
     * @param {string} cameraId - Camera serial number
     */
    clearUserStoppedStream(cameraId) {
        const stopped = this._getUserStoppedStreams();
        if (stopped.has(cameraId)) {
            stopped.delete(cameraId);
            this._saveUserStoppedStreams(stopped);
            console.log(`[UserStopped] Cleared ${cameraId} from user-stopped list. Remaining: ${stopped.size}`);
        }
    }

    /**
     * Check if a camera was manually stopped by user.
     * Recovery logic should skip auto-restarting these streams.
     * @param {string} cameraId - Camera serial number
     * @returns {boolean} True if user manually stopped this stream
     */
    isUserStoppedStream(cameraId) {
        return this._getUserStoppedStreams().has(cameraId);
    }

    /**
     * Clear all user-stopped flags (e.g., on page refresh with "Start All").
     * Not currently used but available for future UI integration.
     */
    clearAllUserStoppedStreams() {
        localStorage.removeItem('userStoppedStreams');
        console.log('[UserStopped] Cleared all user-stopped flags');
    }

    async executePTZ(cameraSerial, direction, $button) {
        try {
            // Visual feedback
            $button.css('background', 'rgba(37, 99, 235, 1)');
            $button.prop('disabled', true);

            const response = await fetch(`/api/ptz/${cameraSerial}/${direction}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });

            const result = await response.json();

            if (!result.success) {
                console.error('PTZ command failed:', result.error);
            }

        } catch (error) {
            console.error('PTZ command error:', error);
        } finally {
            // Reset button after delay
            setTimeout(() => {
                $button.css('background', '');
                $button.prop('disabled', false);
            }, 1000);
        }
    }

    /**
     * Set stream status indicator and text.
     *
     * Respects "Quiet Status Messages" setting - when enabled, only shows important
     * statuses (Starting, Connecting, Live, Failed, Error) and hides verbose ones
     * (Refreshing, Degraded, Recovered, Buffering, Retry, etc.).
     *
     * @param {jQuery} $streamItem - Stream item element
     * @param {string} status - Status class (loading, live, error, failed, active)
     * @param {string} text - Status text to display
     */
    setStreamStatus($streamItem, status, text) {
        const $indicator = $streamItem.find('.stream-indicator');
        const $statusText = $indicator.find('span');

        // Check if quiet mode is enabled
        const quietMode = localStorage.getItem('quietStatusMessages') === 'true';

        if (quietMode) {
            // In quiet mode, only show important statuses
            // Important: Starting, Connecting, Live, Failed, Error, Stopped
            // Verbose (hidden): Refreshing, Degraded, Recovered, Buffering, Retry, Restarting, Nuclear
            const importantPatterns = [
                /^Starting/i,
                /^Connecting/i,
                /^Live/i,
                /^Failed/i,
                /^Error/i,
                /^Stopped/i,
                /^$/  // Empty text (for active status with no message)
            ];

            const isImportant = importantPatterns.some(pattern => pattern.test(text));

            if (!isImportant) {
                // Skip verbose status updates in quiet mode
                // Keep the indicator class updated for visual feedback, just hide the text
                if ($indicator.length) {
                    $indicator.attr('class', `stream-indicator ${status}`);
                }
                // Don't update the text - keep previous important status visible
                return;
            }
        }

        if ($indicator.length) {
            $indicator.attr('class', `stream-indicator ${status}`);
        }
        if ($statusText.length) {
            $statusText.text(text);
        }
    }

    async openFullscreen(cameraId, name, cameraType, streamType) {
        try {
            // Prevent opening if already in fullscreen
            if ($('.stream-item.css-fullscreen').length > 0) {
                console.log('[Fullscreen] Already in fullscreen mode, ignoring');
                return;
            }

            // Close expanded modal if open (entering fullscreen from expanded mode)
            this.collapseExpandedCamera();

            console.log(`[Fullscreen] Opening CSS fullscreen for camera: ${name} (${cameraId})`);

            // Find the stream-item element for this camera
            const $streamItem = $(`.stream-item[data-camera-serial="${cameraId}"]`);

            if ($streamItem.length === 0) {
                throw new Error(`Stream item not found for camera: ${cameraId}`);
            }

            // Apply CSS fullscreen class IMMEDIATELY
            $streamItem.addClass('css-fullscreen');
            localStorage.setItem('fullscreenCameraSerial', cameraId);
            console.log('[Fullscreen] CSS fullscreen activated immediately');

            // iOS SNAPSHOT or portable MJPEG: Switch to HLS for fullscreen
            // In grid view, iOS uses snapshots and other portables use MJPEG for lighter resource usage.
            // In fullscreen, we want HLS for audio support and better quality.
            const urlParams = new URLSearchParams(window.location.search);
            const debugForceMJPEG = urlParams.get('forceMJPEG') === 'true';
            const originalStreamType = $streamItem.data('original-stream-type');

            // Handle SNAPSHOT → HLS switch (iOS grid to fullscreen, or debug ?forceSnapshot mode)
            if (streamType === 'SNAPSHOT' && originalStreamType) {
                console.log(`[Fullscreen] Snapshot mode: Switching ${cameraId} from SNAPSHOT to ${originalStreamType} for fullscreen`);

                // Stop snapshot polling
                this.snapshotManager.stopStream(cameraId);

                // Restore video element if we swapped
                let videoEl;
                if ($streamItem.data('snapshot-swapped')) {
                    console.log(`[Fullscreen] ${cameraId}: Restoring <video> element from snapshot swap`);
                    $streamItem.find('.stream-snapshot-img').remove();
                    const $video = $streamItem.find('video.stream-video');
                    $video.show();
                    videoEl = $video[0];
                    $streamItem.data('snapshot-swapped', false);
                } else {
                    videoEl = $streamItem.find('.stream-video')[0];
                }

                // Start fullscreen stream based on user preference (WebRTC or HLS)
                try {
                    const useWebRTC = isFullscreenWebRTCEnabled();
                    if (useWebRTC) {
                        // User prefers WebRTC for lower latency
                        const dtlsEnabled = await isDTLSEnabled();
                        if (dtlsEnabled || !isIOSDevice()) {
                            // WebRTC available (DTLS for iOS, or non-iOS)
                            await this.webrtcManager.startStream(cameraId, videoEl, 'main');
                            console.log(`[Fullscreen] ✓ Switched to WebRTC main stream for ${cameraId}`);
                            $streamItem.data('fullscreen-stream-type', 'webrtc');
                        } else {
                            // iOS without DTLS - must use HLS
                            console.log(`[Fullscreen] iOS without DTLS - using HLS for ${cameraId}`);
                            await this.hlsManager.startStream(cameraId, videoEl, 'main');
                            $streamItem.data('fullscreen-stream-type', 'hls');
                        }
                    } else {
                        // User prefers HLS (default behavior)
                        await this.hlsManager.startStream(cameraId, videoEl, 'main');
                        console.log(`[Fullscreen] ✓ Switched to HLS main stream for ${cameraId}`);
                        $streamItem.data('fullscreen-stream-type', 'hls');
                    }
                    $streamItem.data('switched-from-snapshot', true);
                    $streamItem.data('switched-to-main', true);
                } catch (e) {
                    console.error(`[Fullscreen] Failed to switch to main stream:`, e);
                    await this.hlsManager.startStream(cameraId, videoEl, 'sub');
                    $streamItem.data('switched-from-snapshot', true);
                    $streamItem.data('fullscreen-stream-type', 'hls');
                }
                return;
            }

            // Handle MJPEG → HLS switch (Android/portable grid to fullscreen)
            if ((isPortableDevice() || debugForceMJPEG) && streamType === 'MJPEG' && originalStreamType) {
                console.log(`[Fullscreen] MJPEG mode: Switching ${cameraId} from MJPEG to ${originalStreamType} for fullscreen`);

                // Stop MJPEG stream
                this.mjpegManager.stopStream(cameraId);

                // If we swapped video→img for MJPEG, restore the original video element
                let videoEl;
                if ($streamItem.data('mjpeg-swapped')) {
                    console.log(`[Fullscreen] ${cameraId}: Restoring <video> element from MJPEG swap`);
                    // Remove the img we created and show the original video
                    $streamItem.find('.stream-mjpeg-img').remove();
                    const $video = $streamItem.find('video.stream-video');
                    $video.show();
                    videoEl = $video[0];
                    $streamItem.data('mjpeg-swapped', false);
                } else {
                    videoEl = $streamItem.find('.stream-video')[0];
                }

                // Start fullscreen stream based on user preference (WebRTC or HLS)
                try {
                    const useWebRTC = isFullscreenWebRTCEnabled();
                    if (useWebRTC) {
                        // User prefers WebRTC for lower latency
                        const dtlsEnabled = await isDTLSEnabled();
                        if (dtlsEnabled || !isIOSDevice()) {
                            // WebRTC available (DTLS for iOS, or non-iOS)
                            await this.webrtcManager.startStream(cameraId, videoEl, 'main');
                            console.log(`[Fullscreen] ✓ Switched to WebRTC main stream for ${cameraId}`);
                            $streamItem.data('fullscreen-stream-type', 'webrtc');
                        } else {
                            // iOS without DTLS - must use HLS
                            console.log(`[Fullscreen] iOS without DTLS - using HLS for ${cameraId}`);
                            await this.hlsManager.startStream(cameraId, videoEl, 'main');
                            $streamItem.data('fullscreen-stream-type', 'hls');
                        }
                    } else {
                        // User prefers HLS (default behavior)
                        await this.hlsManager.startStream(cameraId, videoEl, 'main');
                        console.log(`[Fullscreen] ✓ Switched to HLS main stream for ${cameraId}`);
                        $streamItem.data('fullscreen-stream-type', 'hls');
                    }
                    $streamItem.data('switched-from-mjpeg', true);
                    $streamItem.data('switched-to-main', true);
                } catch (e) {
                    console.error(`[Fullscreen] Failed to switch to main stream:`, e);
                    // Fall back to HLS sub
                    await this.hlsManager.startStream(cameraId, videoEl, 'sub');
                    $streamItem.data('switched-from-mjpeg', true);
                    $streamItem.data('fullscreen-stream-type', 'hls');
                }
                return; // Don't process other stream type switches
            }

            // For LL_HLS/NEOLINK/WEBRTC cameras: Switch to main stream (high-res)
            // The backend dual-output FFmpeg provides both sub and main streams
            // User can choose between HLS and WebRTC for fullscreen via Settings
            if (streamType === 'LL_HLS' || streamType === 'NEOLINK' || streamType === 'WEBRTC') {
                const useWebRTC = isFullscreenWebRTCEnabled();
                console.log(`[Fullscreen] Switching ${cameraId} to main stream (${useWebRTC ? 'WebRTC' : 'HLS'})...`);

                const $video = $streamItem.find('.stream-video');
                const videoEl = $video[0];

                // Stop current stream (could be HLS or WebRTC)
                this.hlsManager.stopStream(cameraId);
                this.webrtcManager.stopStream(cameraId);

                // Start main stream based on user preference
                try {
                    if (useWebRTC) {
                        // User prefers WebRTC for lower latency
                        const dtlsEnabled = await isDTLSEnabled();
                        if (dtlsEnabled || !isIOSDevice()) {
                            await this.webrtcManager.startStream(cameraId, videoEl, 'main');
                            console.log(`[Fullscreen] ✓ Switched to WebRTC main stream for ${cameraId}`);
                            $streamItem.data('fullscreen-stream-type', 'webrtc');
                        } else {
                            // iOS without DTLS - must use HLS
                            console.log(`[Fullscreen] iOS without DTLS - using HLS for ${cameraId}`);
                            await this.hlsManager.startStream(cameraId, videoEl, 'main');
                            $streamItem.data('fullscreen-stream-type', 'hls');
                        }
                    } else {
                        // User prefers HLS (default behavior)
                        await this.hlsManager.startStream(cameraId, videoEl, 'main');
                        console.log(`[Fullscreen] ✓ Switched to HLS main stream for ${cameraId}`);
                        $streamItem.data('fullscreen-stream-type', 'hls');
                    }
                    $streamItem.data('switched-to-main', true);
                } catch (e) {
                    console.error(`[Fullscreen] Failed to switch to main stream:`, e);
                    // Fall back to HLS sub
                    await this.hlsManager.startStream(cameraId, videoEl, 'sub');
                    $streamItem.data('fullscreen-stream-type', 'hls');
                }
            }

            // Pause (not stop) all other streams
            this.pausedStreams = [];
            const $allStreamItems = $('.stream-item');

            for (let i = 0; i < $allStreamItems.length; i++) {
                const $item = $($allStreamItems[i]);
                const id = $item.data('camera-serial');

                if (id === cameraId) continue; // Skip the fullscreen camera

                const $video = $item.find('.stream-video');
                const videoEl = $video[0];
                const itemStreamType = $item.data('stream-type');

                // Pause HLS streams using HLS.js API
                if (itemStreamType === 'HLS' || itemStreamType === 'LL_HLS' || itemStreamType === 'NEOLINK' || itemStreamType === 'NEOLINK_LL_HLS') {
                    const hls = this.hlsManager.hlsInstances.get(id);
                    if (hls && videoEl) {
                        console.log(`[Fullscreen] Pausing HLS stream: ${id}`);
                        hls.stopLoad(); // Stop fetching segments
                        videoEl.pause(); // Stop video decoder

                        // Detach health monitor for paused stream
                        if (videoEl._healthDetach) {
                            videoEl._healthDetach();
                            delete videoEl._healthDetach;
                        }

                        this.pausedStreams.push({ id, type: 'HLS' });
                    }
                }
                // Pause RTMP streams
                else if (itemStreamType === 'RTMP') {
                    if (videoEl) {
                        console.log(`[Fullscreen] Pausing RTMP stream: ${id}`);
                        videoEl.pause();

                        // Detach health monitor for paused stream
                        if (videoEl._healthDetach) {
                            videoEl._healthDetach();
                            delete videoEl._healthDetach;
                        }

                        this.pausedStreams.push({ id, type: 'RTMP' });
                    }
                }
                // Pause MJPEG by stopping image updates
                else if (itemStreamType === 'MJPEG' || itemStreamType === 'mjpeg_proxy') {
                    const imgEl = $video[0];
                    if (imgEl && imgEl.src) {
                        console.log(`[Fullscreen] Pausing MJPEG stream: ${id}`);
                        imgEl._pausedSrc = imgEl.src; // Store src
                        imgEl.src = ''; // Clear to stop fetching

                        // Detach health monitor for paused stream
                        if (imgEl._healthDetach) {
                            imgEl._healthDetach();
                            delete imgEl._healthDetach;
                        }

                        this.pausedStreams.push({ id, type: 'MJPEG' });
                    }
                }
                // Pause SNAPSHOT by stopping polling
                else if (itemStreamType === 'SNAPSHOT') {
                    console.log(`[Fullscreen] Pausing snapshot polling: ${id}`);
                    this.snapshotManager.stopStream(id);

                    const imgEl = $item.find('.stream-snapshot-img')[0] || $video[0];
                    // Detach health monitor for paused stream
                    if (imgEl && imgEl._healthDetach) {
                        imgEl._healthDetach();
                        delete imgEl._healthDetach;
                    }

                    this.pausedStreams.push({ id, type: 'SNAPSHOT', cameraType: $item.data('camera-type') });
                }
                // Pause WebRTC by closing the peer connection (will reconnect on resume)
                else if (itemStreamType === 'WEBRTC') {
                    const stream = this.webrtcManager.activeStreams.get(id);
                    if (stream && videoEl) {
                        console.log(`[Fullscreen] Pausing WebRTC stream: ${id}`);
                        // Store the stream type before stopping
                        videoEl._webrtcStreamType = stream.type || 'sub';
                        this.webrtcManager.stopStream(id);

                        // Detach health monitor for paused stream
                        if (videoEl._healthDetach) {
                            videoEl._healthDetach();
                            delete videoEl._healthDetach;
                        }

                        this.pausedStreams.push({ id, type: 'WEBRTC' });
                    }
                }
            }

            console.log(`[Fullscreen] Paused ${this.pausedStreams.length} streams (still alive, no backend impact)`);

        } catch (error) {
            console.error('[Fullscreen] Failed to open fullscreen:', error);
        }
    }

    async closeFullscreen() {
        try {
            console.log('[Fullscreen] Closing CSS fullscreen...');

            // Find the fullscreen stream item
            const $fullscreenItem = $('.stream-item.css-fullscreen');

            if ($fullscreenItem.length === 0) {
                console.log('[Fullscreen] No fullscreen stream found');
                return;
            }

            const fullscreenCameraId = $fullscreenItem.data('camera-serial');
            const switchedToMain = $fullscreenItem.data('switched-to-main');
            const switchedFromMJPEG = $fullscreenItem.data('switched-from-mjpeg');
            const switchedFromSnapshot = $fullscreenItem.data('switched-from-snapshot');
            const originalStreamType = $fullscreenItem.data('original-stream-type');
            const streamType = $fullscreenItem.data('stream-type');
            const cameraType = $fullscreenItem.data('camera-type');

            // Remove CSS fullscreen class
            $fullscreenItem.removeClass('css-fullscreen');
            console.log('[Fullscreen] CSS fullscreen class removed');

            // Clear localStorage
            localStorage.removeItem('fullscreenCameraSerial');
            console.log('[Fullscreen] Cleared localStorage');

            // iOS: Switch back from HLS/WebRTC to SNAPSHOT for grid view
            if (switchedFromSnapshot && originalStreamType) {
                console.log(`[Fullscreen] iOS: Switching ${fullscreenCameraId} back to SNAPSHOT for grid view`);

                // Stop fullscreen stream (could be HLS or WebRTC depending on user preference)
                const fullscreenStreamType = $fullscreenItem.data('fullscreen-stream-type');
                if (fullscreenStreamType === 'webrtc') {
                    this.webrtcManager.stopStream(fullscreenCameraId);
                } else {
                    this.hlsManager.stopStream(fullscreenCameraId);
                }

                // Snapshots require <img> element
                const $video = $fullscreenItem.find('video.stream-video');
                let imgEl;

                // Check if we need to create an <img> element
                let $existingImg = $fullscreenItem.find('.stream-snapshot-img');
                if ($existingImg.length === 0) {
                    console.log(`[Fullscreen] ${fullscreenCameraId}: Creating <img> element for snapshot`);
                    const $img = $('<img>', {
                        class: 'stream-video stream-snapshot-img',
                        style: 'object-fit: cover; width: 100%; height: 100%;',
                        alt: 'Snapshot Stream'
                    });
                    $video.before($img);
                    $existingImg = $img;
                    $fullscreenItem.data('snapshot-swapped', true);
                }

                // Hide video, show img
                $video.hide();
                $existingImg.show();
                imgEl = $existingImg[0];

                // Start snapshot polling (1 second interval for grid view)
                try {
                    await this.snapshotManager.startStream(fullscreenCameraId, imgEl, cameraType, 1000);
                    console.log(`[Fullscreen] ✓ Switched back to SNAPSHOT for ${fullscreenCameraId}`);
                    // Restore stream-type to SNAPSHOT for grid view
                    $fullscreenItem.data('stream-type', 'SNAPSHOT');
                } catch (e) {
                    console.error(`[Fullscreen] Failed to switch back to SNAPSHOT:`, e);
                }

                // Clear flags
                $fullscreenItem.removeData('switched-from-snapshot');
                $fullscreenItem.removeData('switched-to-main');
            }
            // Portable device (non-iOS): Switch back from HLS/WebRTC to MJPEG for grid view
            else if (switchedFromMJPEG && originalStreamType) {
                console.log(`[Fullscreen] Portable: Switching ${fullscreenCameraId} back to MJPEG for grid view`);

                // Stop fullscreen stream (could be HLS or WebRTC depending on user preference)
                const fullscreenStreamType = $fullscreenItem.data('fullscreen-stream-type');
                if (fullscreenStreamType === 'webrtc') {
                    this.webrtcManager.stopStream(fullscreenCameraId);
                } else {
                    this.hlsManager.stopStream(fullscreenCameraId);
                }

                // MJPEG requires <img> element, not <video>
                // We need to swap video→img for MJPEG to work
                const $video = $fullscreenItem.find('video.stream-video');
                let imgEl;

                // Check if we need to create an <img> element
                let $existingImg = $fullscreenItem.find('.stream-mjpeg-img');
                if ($existingImg.length === 0) {
                    console.log(`[Fullscreen] ${fullscreenCameraId}: Creating <img> element for MJPEG`);
                    const $img = $('<img>', {
                        class: 'stream-video stream-mjpeg-img',
                        style: 'object-fit: cover; width: 100%; height: 100%;',
                        alt: 'MJPEG Stream'
                    });
                    $video.before($img);
                    $existingImg = $img;
                    $fullscreenItem.data('mjpeg-swapped', true);
                }

                // Hide video, show img
                $video.hide();
                $existingImg.show();
                imgEl = $existingImg[0];

                // Start MJPEG stream (sub resolution for grid)
                try {
                    await this.mjpegManager.startStream(fullscreenCameraId, imgEl, cameraType, 'sub');
                    console.log(`[Fullscreen] ✓ Switched back to MJPEG for ${fullscreenCameraId}`);
                    // Restore stream-type to MJPEG for grid view
                    $fullscreenItem.data('stream-type', 'MJPEG');
                } catch (e) {
                    console.error(`[Fullscreen] Failed to switch back to MJPEG:`, e);
                }

                // Clear flags
                $fullscreenItem.removeData('switched-from-mjpeg');
                $fullscreenItem.removeData('switched-to-main');
            }
            // If we switched to main stream, switch back to sub stream
            // Note: Fullscreen may have used WebRTC even if original was HLS (user preference)
            else if (switchedToMain && (streamType === 'LL_HLS' || streamType === 'NEOLINK' || streamType === 'WEBRTC')) {
                const fullscreenStreamType = $fullscreenItem.data('fullscreen-stream-type');
                console.log(`[Fullscreen] Switching ${fullscreenCameraId} back to sub stream (was ${fullscreenStreamType || 'unknown'})...`);

                const $video = $fullscreenItem.find('.stream-video');
                const videoEl = $video[0];

                // Stop fullscreen stream (could be HLS or WebRTC depending on user preference)
                if (fullscreenStreamType === 'webrtc') {
                    this.webrtcManager.stopStream(fullscreenCameraId);
                } else {
                    this.hlsManager.stopStream(fullscreenCameraId);
                }

                // Start sub stream matching the original stream type (grid view uses original type)
                try {
                    if (streamType === 'WEBRTC') {
                        await this.webrtcManager.startStream(fullscreenCameraId, videoEl, 'sub');
                        console.log(`[Fullscreen] ✓ Switched back to WebRTC sub stream for ${fullscreenCameraId}`);
                    } else {
                        await this.hlsManager.startStream(fullscreenCameraId, videoEl, 'sub');
                        console.log(`[Fullscreen] ✓ Switched back to HLS sub stream for ${fullscreenCameraId}`);
                    }
                } catch (e) {
                    console.error(`[Fullscreen] Failed to switch back to sub stream:`, e);
                }

                // Clear the flags
                $fullscreenItem.removeData('switched-to-main');
                $fullscreenItem.removeData('fullscreen-stream-type');
            }

            // Resume previously paused streams
            if (this.pausedStreams && this.pausedStreams.length > 0) {
                console.log(`[Fullscreen] Resuming ${this.pausedStreams.length} paused streams...`);

                for (const stream of this.pausedStreams) {
                    const $item = $(`.stream-item[data-camera-serial="${stream.id}"]`);
                    if (!$item.length) continue;

                    const $video = $item.find('.stream-video');
                    const videoEl = $video[0];
                    const itemStreamType = $item.data('stream-type');

                    if (stream.type === 'HLS') {
                        const hls = this.hlsManager.hlsInstances.get(stream.id);
                        if (hls && videoEl) {
                            console.log(`[Fullscreen] Resuming HLS stream: ${stream.id}`);
                            hls.startLoad(); // Resume fetching segments
                            videoEl.play().catch(e => console.log(`[Fullscreen] Play blocked for ${stream.id}:`, e));

                            // Reattach health monitor
                            this.attachHealthMonitor(stream.id, $item, itemStreamType);
                        }
                    }
                    else if (stream.type === 'RTMP') {
                        if (videoEl) {
                            console.log(`[Fullscreen] Resuming RTMP stream: ${stream.id}`);
                            videoEl.play().catch(e => console.log(`[Fullscreen] Play blocked for ${stream.id}:`, e));

                            // Reattach health monitor
                            this.attachHealthMonitor(stream.id, $item, itemStreamType);
                        }
                    }
                    else if (stream.type === 'MJPEG') {
                        const imgEl = $video[0];
                        if (imgEl && imgEl._pausedSrc) {
                            console.log(`[Fullscreen] Resuming MJPEG stream: ${stream.id}`);
                            imgEl.src = imgEl._pausedSrc; // Restore src to resume fetching
                            delete imgEl._pausedSrc;

                            // Reattach health monitor
                            this.attachHealthMonitor(stream.id, $item, itemStreamType);
                        }
                    }
                    else if (stream.type === 'SNAPSHOT') {
                        // Resume snapshot polling for iOS cameras
                        const imgEl = $item.find('.stream-snapshot-img')[0] || $video[0];
                        if (imgEl) {
                            console.log(`[Fullscreen] Resuming snapshot polling: ${stream.id}`);
                            try {
                                await this.snapshotManager.startStream(stream.id, imgEl, stream.cameraType, 1000);
                                // Reattach health monitor
                                this.attachHealthMonitor(stream.id, $item, itemStreamType);
                            } catch (e) {
                                console.error(`[Fullscreen] Failed to resume snapshot stream ${stream.id}:`, e);
                            }
                        }
                    }
                    else if (stream.type === 'WEBRTC') {
                        if (videoEl) {
                            console.log(`[Fullscreen] Resuming WebRTC stream: ${stream.id}`);
                            // Reconnect WebRTC (it was fully stopped, not just paused)
                            const streamSubType = videoEl._webrtcStreamType || 'sub';
                            delete videoEl._webrtcStreamType;

                            try {
                                await this.webrtcManager.startStream(stream.id, videoEl, streamSubType);
                                // Reattach health monitor
                                this.attachHealthMonitor(stream.id, $item, itemStreamType);
                            } catch (e) {
                                console.error(`[Fullscreen] Failed to resume WebRTC stream ${stream.id}:`, e);
                            }
                        }
                    }
                }

                this.pausedStreams = [];
                console.log('[Fullscreen] All streams resumed');
            }

        } catch (error) {
            console.error('[Fullscreen] Error closing fullscreen:', error);
        }
    }

    // ============================================================================
    // EXPANDED MODAL MODE
    // Intermediate view between grid and fullscreen.
    // Tap camera to expand for easier control access.
    // ============================================================================

    /**
     * Expand a camera card to modal view
     * Shows all control buttons for easy access
     * @param {jQuery} $streamItem - The stream item to expand
     */
    async expandCamera($streamItem) {
        // First exit fullscreen if active (mutual exclusivity with fullscreen mode)
        const $fullscreenItem = $('.stream-item.css-fullscreen');
        if ($fullscreenItem.length > 0) {
            console.log('[Expanded] Exiting fullscreen before expanding');
            await this.closeFullscreen();
        }

        // Then collapse any already expanded camera
        this.collapseExpandedCamera();

        const cameraId = $streamItem.data('camera-serial');
        console.log(`[Expanded] Opening modal for ${cameraId}`);

        // Show backdrop
        $('#expanded-backdrop').addClass('visible');

        // Add expanded class to stream item
        $streamItem.addClass('expanded');

        // Prevent body scroll while modal is open
        $('body').css('overflow', 'hidden');
    }

    /**
     * Collapse the expanded camera back to grid view
     */
    collapseExpandedCamera() {
        const $expandedItem = $('.stream-item.expanded');

        if ($expandedItem.length === 0) {
            return;
        }

        const cameraId = $expandedItem.data('camera-serial');
        console.log(`[Expanded] Collapsing modal for ${cameraId}`);

        // CRITICAL: Hide PTZ controls when collapsing to grid view
        // PTZ controls are too large for grid thumbnails and block interaction
        const $ptzControls = $expandedItem.find('.ptz-controls');
        if ($ptzControls.hasClass('ptz-visible')) {
            $ptzControls.removeClass('ptz-visible');
            $expandedItem.find('.stream-ptz-toggle-btn').removeClass('ptz-active');
            console.log(`[Expanded] ${cameraId}: Hiding PTZ controls on collapse`);
        }

        // Also hide stream controls panel if visible
        const $streamControls = $expandedItem.find('.stream-controls');
        if ($streamControls.hasClass('stream-controls-visible')) {
            $streamControls.removeClass('stream-controls-visible');
            $expandedItem.find('.stream-controls-toggle-btn').removeClass('controls-active');
        }

        // Hide backdrop
        $('#expanded-backdrop').removeClass('visible');

        // Remove expanded class
        $expandedItem.removeClass('expanded');

        // Restore body scroll
        $('body').css('overflow', '');
    }

    async restoreFullscreenFromLocalStorage() {
        const savedCameraId = localStorage.getItem('fullscreenCameraSerial');

        if (!savedCameraId) {
            console.log('[Fullscreen] No saved fullscreen camera found');
            return;
        }

        console.log(`[Fullscreen] Found saved camera in localStorage: ${savedCameraId}`);
        console.log('[Fullscreen] Restoring CSS fullscreen (no user interaction required)...');

        // Find the stream-item
        const $streamItem = $(`.stream-item[data-camera-serial="${savedCameraId}"]`);

        if ($streamItem.length === 0) {
            console.warn(`[Fullscreen] Saved camera ${savedCameraId} not found in DOM`);
            localStorage.removeItem('fullscreenCameraSerial');
            return;
        }

        const name = $streamItem.data('camera-name');
        const cameraType = $streamItem.data('camera-type');
        const streamType = $streamItem.data('stream-type');

        // Restore CSS fullscreen immediately (no user gesture needed!)
        await this.openFullscreen(savedCameraId, name, cameraType, streamType);

        console.log('[Fullscreen] CSS fullscreen restored successfully');
    }

    // =========================================================================
    // iOS PAGINATION METHODS
    // iOS Safari has hard limits on simultaneous video decodes (~4-8).
    // This pagination system shows max 6 cameras per page on iOS devices.
    // =========================================================================

    /**
     * Initialize iOS pagination system.
     * Creates page controls, collects camera IDs, and sets up initial page view.
     */
    initIOSPagination() {
        console.log('[iOS Pagination] Initializing...');

        // Collect all camera IDs in DOM order
        const $streamItems = this.$container.find('.stream-item');
        this.iosPagination.allCameraIds = $streamItems.toArray().map(
            item => $(item).data('camera-serial')
        );

        const totalCameras = this.iosPagination.allCameraIds.length;
        this.iosPagination.totalPages = Math.ceil(totalCameras / this.iosPagination.camerasPerPage);

        console.log(`[iOS Pagination] ${totalCameras} cameras, ${this.iosPagination.totalPages} pages (${this.iosPagination.camerasPerPage} per page)`);

        // Create pagination controls UI
        this.createPaginationControls();

        // Show only first page cameras, hide the rest
        this.showPage(0);
    }

    /**
     * Create pagination controls (prev/next buttons, page indicator).
     * Inserted above the streams container.
     */
    createPaginationControls() {
        // Remove existing controls if any (for re-initialization)
        $('#ios-pagination-controls').remove();

        const $controls = $(`
            <div id="ios-pagination-controls" class="ios-pagination-controls">
                <button id="ios-prev-page" class="ios-page-btn" aria-label="Previous page">
                    <i class="fas fa-chevron-left"></i>
                </button>
                <span id="ios-page-indicator" class="ios-page-indicator">
                    Page 1 / ${this.iosPagination.totalPages}
                </span>
                <button id="ios-next-page" class="ios-page-btn" aria-label="Next page">
                    <i class="fas fa-chevron-right"></i>
                </button>
            </div>
        `);

        // Insert before streams container
        this.$container.before($controls);

        // Bind button events
        $('#ios-prev-page').on('click', () => this.goToPage(this.iosPagination.currentPage - 1));
        $('#ios-next-page').on('click', () => this.goToPage(this.iosPagination.currentPage + 1));

        // Update button states
        this.updatePaginationButtons();
    }

    /**
     * Navigate to a specific page.
     * Stops streams on current page, shows new page, starts streams on new page.
     *
     * @param {number} pageIndex - Zero-based page index
     */
    async goToPage(pageIndex) {
        // Bounds check
        if (pageIndex < 0 || pageIndex >= this.iosPagination.totalPages) {
            console.log(`[iOS Pagination] Page ${pageIndex} out of bounds`);
            return;
        }

        if (pageIndex === this.iosPagination.currentPage) {
            console.log(`[iOS Pagination] Already on page ${pageIndex}`);
            return;
        }

        console.log(`[iOS Pagination] Navigating from page ${this.iosPagination.currentPage} to ${pageIndex}`);

        // Stop streams on current page before switching
        await this.stopCurrentPageStreams();

        // Update page and show new cameras
        this.showPage(pageIndex);

        // Start streams on new page
        await this.startCurrentPageStreams();
    }

    /**
     * Show cameras for the specified page, hide all others.
     *
     * @param {number} pageIndex - Zero-based page index
     */
    showPage(pageIndex) {
        this.iosPagination.currentPage = pageIndex;

        const start = pageIndex * this.iosPagination.camerasPerPage;
        const end = start + this.iosPagination.camerasPerPage;
        const visibleCameras = this.iosPagination.allCameraIds.slice(start, end);

        console.log(`[iOS Pagination] Showing page ${pageIndex}: cameras ${start}-${end - 1}`);

        // Show/hide stream items based on page
        this.$container.find('.stream-item').each((_, item) => {
            const $item = $(item);
            const cameraId = $item.data('camera-serial');

            if (visibleCameras.includes(cameraId)) {
                $item.removeClass('ios-hidden').show();
            } else {
                $item.addClass('ios-hidden').hide();
            }
        });

        // Update UI
        this.updatePaginationButtons();
        $('#ios-page-indicator').text(`Page ${pageIndex + 1} / ${this.iosPagination.totalPages}`);
    }

    /**
     * Stop all streams on the current page.
     * Called before page navigation to free up video decode resources.
     */
    async stopCurrentPageStreams() {
        const start = this.iosPagination.currentPage * this.iosPagination.camerasPerPage;
        const end = start + this.iosPagination.camerasPerPage;
        const currentPageCameras = this.iosPagination.allCameraIds.slice(start, end);

        console.log(`[iOS Pagination] Stopping ${currentPageCameras.length} streams on page ${this.iosPagination.currentPage}`);

        for (const cameraId of currentPageCameras) {
            const $streamItem = $(`.stream-item[data-camera-serial="${cameraId}"]`);
            if ($streamItem.length) {
                const cameraType = $streamItem.data('camera-type');
                const streamType = $streamItem.data('stream-type');
                await this.stopIndividualStream(cameraId, $streamItem, cameraType, streamType);
            }
        }
    }

    /**
     * Start streams on the current page.
     * Uses sequential loading with delays to avoid overwhelming iOS Safari.
     */
    async startCurrentPageStreams() {
        const start = this.iosPagination.currentPage * this.iosPagination.camerasPerPage;
        const end = start + this.iosPagination.camerasPerPage;
        const currentPageCameras = this.iosPagination.allCameraIds.slice(start, end);

        console.log(`[iOS Pagination] Starting ${currentPageCameras.length} streams on page ${this.iosPagination.currentPage}`);

        // Sequential with 500ms delay between each for iOS
        for (let i = 0; i < currentPageCameras.length; i++) {
            const cameraId = currentPageCameras[i];
            const $streamItem = $(`.stream-item[data-camera-serial="${cameraId}"]`);

            if ($streamItem.length) {
                const cameraType = $streamItem.data('camera-type');
                const streamType = $streamItem.data('stream-type');

                console.log(`[iOS Pagination] Starting stream ${i + 1}/${currentPageCameras.length}: ${cameraId}`);

                try {
                    await this.startStream(cameraId, $streamItem, cameraType, streamType);
                } catch (error) {
                    console.error(`[iOS Pagination] Failed to start ${cameraId}:`, error);
                    this.setStreamStatus($streamItem, 'error', 'Failed to load');
                }

                // Delay between starts (except for last one)
                if (i < currentPageCameras.length - 1) {
                    await new Promise(r => setTimeout(r, 500));
                }
            }
        }
    }

    /**
     * Update prev/next button enabled states based on current page.
     */
    updatePaginationButtons() {
        const { currentPage, totalPages } = this.iosPagination;

        $('#ios-prev-page').prop('disabled', currentPage === 0);
        $('#ios-next-page').prop('disabled', currentPage >= totalPages - 1);
    }

    /**
     * Get cameras visible on current page.
     * Used by startAllStreams to only start visible cameras on iOS.
     *
     * @returns {string[]} Array of camera IDs on current page
     */
    getVisibleCameraIds() {
        if (!this.iosPagination.enabled) {
            // Non-iOS: all cameras are visible
            return this.iosPagination.allCameraIds;
        }

        const start = this.iosPagination.currentPage * this.iosPagination.camerasPerPage;
        const end = start + this.iosPagination.camerasPerPage;
        return this.iosPagination.allCameraIds.slice(start, end);
    }

    // ==========================================================================
    // Stream Events WebSocket - Real-time restart notifications from backend
    // ==========================================================================

    /**
     * Connect to backend stream events WebSocket.
     *
     * Receives real-time notifications when StreamWatchdog restarts a stream,
     * triggering immediate HLS refresh instead of waiting for 10-second poll cycle.
     * This solves the "black screen after backend restart" issue where HLS.js
     * stays connected to a stale MediaMTX session.
     */
    async connectStreamEventsSocket() {
        // Load Socket.IO client if not already loaded
        if (typeof io === 'undefined') {
            await this._loadSocketIOClient();
        }

        // Track consecutive failures for logging
        this.wsReconnectAttempts = 0;

        try {
            this.streamEventsSocket = io('/stream_events', {
                transports: ['websocket', 'polling'],
                reconnection: true,
                reconnectionAttempts: Infinity,  // Never give up - always try to reconnect
                reconnectionDelay: 1000,         // Start with 1 second
                reconnectionDelayMax: 30000,     // Max 30 seconds between attempts
                timeout: 10000
            });

            this.streamEventsSocket.on('connect', () => {
                console.log('[WEBSOCKET] Connected to /stream_events namespace');
                // Enable WebSocket-based recovery, disable poll-based fallback
                this.wsRecoveryEnabled = true;
                this.wsReconnectAttempts = 0;
                console.log('[WEBSOCKET] Recovery mode: WebSocket (instant notifications)');
            });

            this.streamEventsSocket.on('connected', (data) => {
                console.log('[WEBSOCKET] Server confirmed subscription:', data);
            });

            // Track recent recovery attempts to debounce rapid-fire events
            this.recentRecoveries = this.recentRecoveries || new Map();

            this.streamEventsSocket.on('stream_restarted', (data) => {
                const { camera_id, timestamp } = data;
                console.log(`[WEBSOCKET] stream_restarted event received for ${camera_id} at ${new Date(timestamp * 1000).toLocaleTimeString()}`);

                // Skip if this camera is undergoing manual restart via UI button
                // The manual restart handler has its own retry logic and timing
                if (this.manualRestartInProgress?.has(camera_id)) {
                    console.log(`[WEBSOCKET] Ignoring event for ${camera_id} (manual restart in progress)`);
                    return;
                }

                // Debounce: ignore if we handled recovery for this camera in last 5 seconds
                const lastRecovery = this.recentRecoveries.get(camera_id);
                const now = Date.now();
                if (lastRecovery && (now - lastRecovery) < 5000) {
                    console.log(`[WEBSOCKET] Ignoring duplicate event for ${camera_id} (debounced)`);
                    return;
                }

                // Find the stream item and trigger recovery
                const $streamItem = $(`.stream-item[data-camera-serial="${camera_id}"]`);
                if ($streamItem.length) {
                    console.log(`[WEBSOCKET] Triggering refresh for ${camera_id}`);
                    this.recentRecoveries.set(camera_id, now);
                    this.handleBackendRecovery(camera_id, $streamItem);
                } else {
                    console.log(`[WEBSOCKET] Camera ${camera_id} not on this page, ignoring`);
                }
            });

            this.streamEventsSocket.on('disconnect', (reason) => {
                console.warn(`[WEBSOCKET] Disconnected from /stream_events: ${reason}`);
                // Disable WebSocket recovery, enable poll-based fallback
                this.wsRecoveryEnabled = false;
                console.log('[WEBSOCKET] Recovery mode: Polling fallback (10-second delay)');
            });

            this.streamEventsSocket.on('connect_error', (error) => {
                this.wsReconnectAttempts++;
                // Only log every 5th attempt to avoid console spam
                if (this.wsReconnectAttempts === 1 || this.wsReconnectAttempts % 5 === 0) {
                    console.error(`[WEBSOCKET] Connection error (attempt ${this.wsReconnectAttempts}):`, error.message || error);
                }
                // Ensure fallback is active
                if (this.wsRecoveryEnabled) {
                    this.wsRecoveryEnabled = false;
                    console.log('[WEBSOCKET] Recovery mode: Polling fallback (WebSocket connection failed)');
                }
            });

            // Socket.IO reconnection events
            this.streamEventsSocket.on('reconnect', (attemptNumber) => {
                console.log(`[WEBSOCKET] Reconnected after ${attemptNumber} attempts`);
                this.wsRecoveryEnabled = true;
                console.log('[WEBSOCKET] Recovery mode: WebSocket (reconnected)');
            });

            this.streamEventsSocket.on('reconnect_attempt', (attemptNumber) => {
                // Log occasionally to show reconnection is being attempted
                if (attemptNumber === 1 || attemptNumber % 10 === 0) {
                    console.log(`[WEBSOCKET] Reconnection attempt ${attemptNumber}...`);
                }
            });

        } catch (error) {
            console.error('[WEBSOCKET] Failed to initialize:', error);
            // Ensure fallback is active on initialization failure
            this.wsRecoveryEnabled = false;
        }
    }

    /**
     * Load Socket.IO client library dynamically if not already loaded.
     *
     * @returns {Promise<void>}
     */
    _loadSocketIOClient() {
        return new Promise((resolve, reject) => {
            if (typeof io !== 'undefined') {
                resolve();
                return;
            }

            const script = document.createElement('script');
            script.src = 'https://cdn.socket.io/4.7.4/socket.io.min.js';
            script.crossOrigin = 'anonymous';
            script.onload = () => {
                console.log('[WEBSOCKET] Socket.IO client loaded');
                resolve();
            };
            script.onerror = () => {
                reject(new Error('Failed to load Socket.IO client'));
            };
            document.head.appendChild(script);
        });
    }

    updateStreamCount() {
        const count = this.$container.find('.stream-item').length;
        this.$streamCount.text(`${count} camera${count !== 1 ? 's' : ''}`);
    }
}

// Initialize when page loads
$(document).ready(() => {
    // Expose globally for debugging and console access
    window.streamManager = new MultiStreamManager();
});
