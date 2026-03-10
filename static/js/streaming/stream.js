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
import { talkbackManager } from './talkback-manager.js';
import { VisibilityManager } from './visibility-manager.js';
import { pinnedWindowManager } from './pinned-window-manager.js';
import { tileArrangeManager } from './tile-arrange-manager.js';

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
 * Check if mobile devices should force WebRTC in grid view.
 * When enabled, bypasses the default MJPEG (Android) or snapshot (iOS)
 * override, giving real-time WebRTC video (~200ms latency) in grid view.
 *
 * Applies to all portable devices (iOS, Android, etc.)
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
            onDegraded: (cameraId, $streamItem, previousState, newState) => {
                // When a camera goes offline/degraded, stop snapshot polling immediately.
                // Prevents stale frozen frames from being retained and stops unnecessary requests.
                if (this.snapshotManager.isStreamActive(cameraId)) {
                    console.log(`[CameraState] ${cameraId}: ${newState} — stopping snapshot polling`);
                    this.snapshotManager.stopStream(cameraId);
                }
            },
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

                    // CHECK BACKEND STATE: If the backend watchdog already knows
                    // the stream is down (degraded/offline), defer to it instead of
                    // scheduling duplicate UI restarts that conflict with backend recovery
                    const backendState = this.cameraStateMonitor?.previousStates?.get(cameraId);
                    if (backendState && (backendState === 'degraded' || backendState === 'offline')) {
                        console.log(
                            `[Health] ${cameraId}: Backend already aware (state: ${backendState}), ` +
                            `deferring to watchdog - skipping UI restart`
                        );
                        return;
                    }

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

        // Reset all control states FIRST to prevent stale cache states
        // This clears PTZ panels, audio unmute, stream controls, talkback buttons
        this.resetAllControlStates();

        this.setupLayout();
        this.setupEventListeners();
        this.updateStreamCount();

        // Initialize iOS pagination if enabled
        if (this.iosPagination.enabled) {
            this.initIOSPagination();
        }

        // Pre-fetch user preferences to get default_video_fit and pinned_camera.
        // Runs in parallel with stream loading.
        fetch('/api/my-preferences')
            .then(r => r.json())
            .then(prefs => {
                // --- Video fit default ---
                const fit = prefs.default_video_fit || 'cover';
                if (window.VIDEO_FIT_DEFAULT !== fit) {
                    window.VIDEO_FIT_DEFAULT = fit;
                    this.$container.find('.stream-item').each((_, item) => {
                        const $item = $(item);
                        if (!$item.data('video-fit')) {
                            $item.find('.stream-video').css('object-fit', fit);
                        }
                    });
                }

                // --- Pinned camera: sync localStorage from DB (DB is source of truth) ---
                const pinnedFromDB = prefs.pinned_camera || null;
                if (pinnedFromDB) {
                    localStorage.setItem('pinnedCamera', pinnedFromDB);
                } else {
                    localStorage.removeItem('pinnedCamera');
                }

                // Merge DB window positions into pinnedWindowManager (DB is authoritative)
                if (prefs.pinned_windows && typeof prefs.pinned_windows === 'object') {
                    pinnedWindowManager.mergeFromDB(prefs.pinned_windows);
                }

                // Auto-expand pinned camera after streams have had time to start
                // (2s delay — streams may still be loading on first render)
                if (pinnedFromDB) {
                    setTimeout(async () => {
                        const $tile = this.$container.find(`.stream-item[data-camera-serial="${pinnedFromDB}"]`);
                        if (!$tile.length || $tile.hasClass('expanded') || pinnedWindowManager.isActive(pinnedFromDB)) return;

                        // If camera is also HD, open as floating window; otherwise expand as modal
                        const isHD = (() => {
                            try {
                                return (JSON.parse(localStorage.getItem('hdCameras') || '[]')).includes(pinnedFromDB);
                            } catch { return false; }
                        })();

                        if (isHD) {
                            console.log(`[Pin] Auto-activating floating window for pinned+HD camera: ${pinnedFromDB}`);
                            pinnedWindowManager.activate(pinnedFromDB, $tile);
                        } else {
                            console.log(`[Pin] Auto-expanding pinned camera: ${pinnedFromDB}`);
                            await this.expandCamera($tile);
                        }
                    }, 2000);
                }
            })
            .catch(() => { /* non-critical */ });

        // Pre-fetch streaming config (caches DTLS setting for iOS WebRTC check)
        // This runs in parallel with stream loading so config is ready when needed
        getStreamingConfig().then(config => {
            console.log(`[Init] Streaming config: DTLS=${config?.webrtc?.encryption_enabled}`);
        }).catch(err => {
            console.warn('[Init] Failed to pre-fetch streaming config:', err);
        });

        // Load user stream type preferences, then start all streams.
        // Preferences override the data-stream-type set by cameras.json so each
        // user gets their saved stream type per camera (WEBRTC, HLS, MJPEG, etc.)
        console.log('[Init] Loading user stream preferences then starting streams...');
        this.loadUserStreamPreferences().then(() => {
            return this.startAllStreams();
        }).then(() => {
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

        // Visibility manager: detect monitor standby/off, tear down streams to
        // save bandwidth, show hypnotic overlay, reload page on wake
        this.visibilityManager = new VisibilityManager({
            graceMs: 3000,       // Ignore tab switches < 3 seconds
            reloadDelayMs: 1800, // Show wake animation before reload
            onSleep: () => {
                console.log('[Visibility] Tearing down all browser-side stream consumers');
                // Stop all stream types (HLS.js, WebRTC PeerConnections, MJPEG img polling)
                this.stopAllStreams();
                // Stop health monitor (no point checking dead streams)
                if (this.health) {
                    this.trackers?.forEach((_, serial) => this.health.detach(serial));
                }
                // Stop camera state polling
                this.cameraStateMonitor?.stop();
                // Disconnect stream events WebSocket
                if (this.streamEventsSocket) {
                    this.streamEventsSocket.disconnect();
                }
            }
        });
        this.visibilityManager.start();
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


        // Set aspect ratio: 13/8 = 1.625 — consecutive Fibonacci numbers, golden ratio approx (φ ≈ 1.618)
        $streamItems.css('aspect-ratio', '13/8');

        // Apply video fit mode to each tile's media element.
        // Priority: per-camera data-video-fit attr > user default (window.VIDEO_FIT_DEFAULT) > 'cover'
        const userDefault = window.VIDEO_FIT_DEFAULT || 'cover';
        $streamItems.each((_, item) => {
            const $item = $(item);
            const fit = $item.data('video-fit') || userDefault;
            $item.find('.stream-video').css('object-fit', fit);
        });
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

            // Safety net: force-reset the processing flag after 2 seconds.
            // If closeFullscreen() hangs (e.g., frozen video decoder blocking event loop),
            // this ensures the button isn't permanently blocked.
            clearTimeout(this._fullscreenProcessingWatchdog);
            this._fullscreenProcessingWatchdog = setTimeout(() => {
                if (this._fullscreenProcessing) {
                    console.warn('[Fullscreen] Processing flag stuck for 2s - force resetting');
                    this._fullscreenProcessing = false;
                }
            }, 2000);

            window._fullscreenLocked = true;
            console.log('[Fullscreen] Lock acquired');

            try {
                const $streamItem = $(e.target).closest('.stream-item');
                if (!$streamItem.length) return;

                // Check current state
                const isCurrentlyFullscreen = $streamItem.hasClass('css-fullscreen');
                const isCurrentlyExpanded = $streamItem.hasClass('expanded');
                console.log(`[Fullscreen] Button clicked - state: ${isCurrentlyFullscreen ? 'FULLSCREEN' : isCurrentlyExpanded ? 'EXPANDED' : 'GRID'}`);

                if (isCurrentlyFullscreen) {
                    // Use synchronous force-exit to avoid hanging on frozen streams.
                    // closeFullscreen() is async and can block if the video decoder is stuck.
                    // forceExitFullscreen() removes the CSS class immediately, then runs
                    // deferred async cleanup via setTimeout.
                    this.forceExitFullscreen();
                    console.log('[Fullscreen] Exit complete');
                } else {
                    // Enter fullscreen from either grid or expanded modal
                    const cameraId = $streamItem.data('camera-serial');
                    const name = $streamItem.data('camera-name');
                    const cameraType = $streamItem.data('camera-type');
                    const streamType = $streamItem.data('stream-type');

                    // If coming from expanded modal, collapse it first so fullscreen
                    // starts clean (the modal overlay and expanded class are removed)
                    if (isCurrentlyExpanded) {
                        this.collapseExpandedCamera();
                    }

                    await this.openFullscreen(cameraId, name, cameraType, streamType);
                    console.log(`[Fullscreen] Enter complete (from ${isCurrentlyExpanded ? 'expanded' : 'grid'})`);
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

        // PTZ control handlers - REMOVED (Jan 24, 2026)
        // This duplicate handler was causing double-action bug when Rev Pan was enabled.
        // PTZ is now handled exclusively by ptz-controller.js which applies reversal correctly.
        // See: ptz-controller.js:339 for the authoritative mousedown/touchstart handler.

        // Refresh stream handler (client-side reconnect — no backend call)
        //
        // Shows the stream-reload-overlay while HLS.js / WebRTC tears down and
        // reconnects.  Step messages keep the user informed during what would
        // otherwise be a silent black tile.
        //
        // NOTE: Only HLS and WebRTC streams can be force-refreshed here.
        // Adding other types without this guard would spawn a new RTSP source
        // while the old RTMP publisher is still running — causing stream conflict.
        this.$container.on('click', '.refresh-stream-btn', (e) => {
            e.stopPropagation();
            const $streamItem = $(e.target).closest('.stream-item');
            const cameraId = $streamItem.data('camera-serial');
            const streamType = $streamItem.data('stream-type');
            const videoElement = $streamItem.find('.stream-video')[0];

            // Show overlay immediately so the user gets instant feedback
            this._showStreamReloadOverlay($streamItem, 'refresh');
            this._logStreamReloadStep($streamItem, 'Client refresh requested — tearing down current connection...', 'info');

            if (streamType === 'HLS' || streamType === 'LL_HLS' || streamType === 'NEOLINK' || streamType === 'NEOLINK_LL_HLS') {
                this._logStreamReloadStep($streamItem, 'Stopping HLS.js segment fetch...', 'info');
                this._logStreamReloadStep($streamItem, 'Destroying HLS.js instance and resetting video element...', 'info');

                Promise.resolve(this.hlsManager.forceRefreshStream(cameraId, videoElement))
                    .then(() => {
                        this._logStreamReloadStep($streamItem, 'HLS.js instance rebuilt — requesting fresh playlist from MediaMTX...', 'info');
                        this._logStreamReloadStep($streamItem, 'Waiting for first segment to arrive...', 'info');
                        this._logStreamReloadStep($streamItem, 'Stream live!', 'success');
                        this._hideStreamReloadOverlay($streamItem, /* waking= */ true);
                    })
                    .catch((err) => {
                        this._logStreamReloadStep($streamItem, `Refresh failed: ${err.message}`, 'error');
                        // Leave error visible for 3 s then dismiss
                        setTimeout(() => this._hideStreamReloadOverlay($streamItem), 3000);
                    });

            } else if (streamType === 'WEBRTC') {
                this._logStreamReloadStep($streamItem, 'Closing WebRTC peer connection and ICE candidates...', 'info');
                this._logStreamReloadStep($streamItem, 'Initiating new WebRTC offer/answer handshake with MediaMTX...', 'info');

                Promise.resolve(this.webrtcManager.forceRefreshStream(cameraId, videoElement))
                    .then(() => {
                        this._logStreamReloadStep($streamItem, 'WebRTC peer connection established — stream live!', 'success');
                        this._hideStreamReloadOverlay($streamItem, /* waking= */ true);
                    })
                    .catch((err) => {
                        this._logStreamReloadStep($streamItem, `Refresh failed: ${err.message}`, 'error');
                        setTimeout(() => this._hideStreamReloadOverlay($streamItem), 3000);
                    });

            } else {
                // Stream type not supported for client-side refresh
                this._logStreamReloadStep($streamItem, `Stream type "${streamType}" does not support client-side refresh.`, 'warn');
                setTimeout(() => this._hideStreamReloadOverlay($streamItem), 2500);
            }
        });

        // Track cameras currently undergoing manual restart (to block WebSocket auto-recovery)
        this.manualRestartInProgress = this.manualRestartInProgress || new Set();

        // Restart stream handler (backend FFmpeg restart)
        //
        // Unlike refresh (which only reconnects the client-side player), this sends
        // a POST to /api/stream/restart which kills the running FFmpeg process and
        // spawns a fresh one.  The camera's RTSP connection is fully re-established.
        //
        // Flow:
        //   1. POST /api/stream/restart → backend kills FFmpeg
        //   2. Wait ~3 s for FFmpeg to reconnect to the camera and publish to MediaMTX
        //   3. Reconnect HLS.js / WebRTC with up to 3 retries (2 s apart)
        //   4. Dismiss overlay with "waking" animation on success
        //
        // The reload overlay is shown throughout with step-by-step log messages so
        // the user knows exactly what is happening during the multi-second wait.
        this.$container.on('click', '.restart-stream-btn', async (e) => {
            e.stopPropagation();
            const $streamItem = $(e.target).closest('.stream-item');
            const cameraId = $streamItem.data('camera-serial');
            const streamType = $streamItem.data('stream-type');
            const videoElement = $streamItem.find('.stream-video')[0];

            // MJPEG streams are stateless (no backend FFmpeg process to restart).
            // Instead of doing nothing, refresh the MJPEG image connection.
            if (streamType === 'MJPEG') {
                console.log(`[Restart] ${cameraId}: MJPEG is stateless, refreshing stream`);
                this._showStreamReloadOverlay($streamItem, 'refresh');
                this._logStreamReloadStep($streamItem, 'MJPEG is stateless — no FFmpeg process to restart.', 'info');
                this._logStreamReloadStep($streamItem, 'Dropping current MJPEG image connection...', 'info');
                this.setStreamStatus($streamItem, 'loading', 'Refreshing...');
                if (this.mjpegManager) {
                    this.mjpegManager.stopStream(cameraId);
                    const imgEl = $streamItem.find('img.mjpeg-stream')[0];
                    if (imgEl) {
                        const quality = $streamItem.hasClass('hd-mode') ? 'main' : 'sub';
                        this._logStreamReloadStep($streamItem, `Reconnecting MJPEG stream (quality: ${quality})...`, 'info');
                        await this.mjpegManager.startStream(cameraId, imgEl, quality);
                        this._logStreamReloadStep($streamItem, 'MJPEG stream reconnected — live!', 'success');
                        this.setStreamStatus($streamItem, 'live', 'Live');
                    }
                }
                this._hideStreamReloadOverlay($streamItem, /* waking= */ true);
                return;
            }

            // Show overlay immediately — the restart takes several seconds and the
            // user needs to know something is happening from the first click.
            this._showStreamReloadOverlay($streamItem, 'restart');
            this._logStreamReloadStep($streamItem, 'Backend restart requested — preparing to terminate FFmpeg process...', 'info');

            // Mark this camera as undergoing manual restart.
            // This prevents WebSocket stream_restarted events from triggering
            // the auto-recovery path and interfering with our controlled flow.
            this.manualRestartInProgress.add(cameraId);
            console.log(`[Restart] ${cameraId}: Initiating backend FFmpeg restart...`);
            this.setStreamStatus($streamItem, 'loading', 'Restarting...');

            try {
                // Step 1: Tell backend to kill + respawn FFmpeg
                this._logStreamReloadStep($streamItem, 'Sending restart signal to backend API...', 'info');
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
                this._logStreamReloadStep($streamItem, 'Backend acknowledged — FFmpeg process terminated.', 'info');
                this._logStreamReloadStep($streamItem, 'Spawning new FFmpeg instance and connecting to camera RTSP source...', 'info');

                // Step 2: Wait for FFmpeg to establish its connection to the camera
                // and begin publishing the HLS/WebRTC stream to MediaMTX.
                // FFmpeg typically needs 2-3 s to negotiate RTSP and start streaming.
                this._logStreamReloadStep($streamItem, 'Waiting for FFmpeg to publish to MediaMTX (~3 s)...', 'info');
                await new Promise(r => setTimeout(r, 3000));

                // Step 3: Reconnect the client-side player with retry logic.
                // MediaMTX may not have the path ready immediately after FFmpeg starts,
                // so we retry up to 3 times with 2 s between attempts.
                const maxRetries = 3;
                const retryDelay = 2000;

                for (let attempt = 1; attempt <= maxRetries; attempt++) {
                    try {
                        this._logStreamReloadStep($streamItem,
                            `Reconnecting ${streamType} player (attempt ${attempt}/${maxRetries})...`, 'info');
                        console.log(`[Restart] ${cameraId}: Reconnecting (attempt ${attempt}/${maxRetries})...`);

                        if (streamType === 'HLS' || streamType === 'LL_HLS' || streamType === 'NEOLINK' || streamType === 'NEOLINK_LL_HLS') {
                            this._logStreamReloadStep($streamItem, 'Rebuilding HLS.js instance and requesting fresh playlist...', 'info');
                            await this.hlsManager.forceRefreshStream(cameraId, videoElement);
                        } else if (streamType === 'WEBRTC') {
                            this._logStreamReloadStep($streamItem, 'Initiating new WebRTC offer/answer handshake with MediaMTX...', 'info');
                            await this.webrtcManager.forceRefreshStream(cameraId, videoElement);
                        }

                        // Success — exit retry loop
                        break;
                    } catch (retryError) {
                        console.warn(`[Restart] ${cameraId}: Attempt ${attempt} failed: ${retryError.message}`);
                        if (attempt < maxRetries) {
                            this._logStreamReloadStep($streamItem,
                                `Attempt ${attempt} failed — MediaMTX path not ready yet. Retrying in ${retryDelay / 1000} s...`, 'warn');
                            console.log(`[Restart] ${cameraId}: Waiting ${retryDelay}ms before retry...`);
                            await new Promise(r => setTimeout(r, retryDelay));
                        } else {
                            throw retryError; // All retries exhausted — propagate to catch block
                        }
                    }
                }

                // Step 4: All done — animate the overlay out with the "waking" transition
                this._logStreamReloadStep($streamItem, 'Stream fully restarted — live!', 'success');
                this.setStreamStatus($streamItem, 'active', '');
                console.log(`[Restart] ${cameraId}: Stream fully restarted`);
                this._hideStreamReloadOverlay($streamItem, /* waking= */ true);

            } catch (error) {
                console.error(`[Restart] ${cameraId}: Failed - ${error.message}`);
                this._logStreamReloadStep($streamItem, `Restart failed: ${error.message}`, 'error');
                this.setStreamStatus($streamItem, 'error', `Restart failed: ${error.message}`);
                // Leave error message visible for 4 s then dismiss
                setTimeout(() => this._hideStreamReloadOverlay($streamItem), 4000);
            } finally {
                // Clear manual restart flag regardless of success/failure so the
                // WebSocket recovery path can resume normal operation.
                this.manualRestartInProgress.delete(cameraId);
                console.log(`[Restart] ${cameraId}: Manual restart flow completed`);
            }
        });

        // Reboot camera handler (requires confirmation)
        this.$container.on('click', '.reboot-camera-btn', async (e) => {
            e.stopPropagation();
            const $streamItem = $(e.target).closest('.stream-item');
            const cameraId = $streamItem.data('camera-serial');
            const cameraName = $streamItem.data('camera-name');

            // Show confirmation dialog
            const confirmed = window.confirm(
                `Are you sure you want to reboot "${cameraName}"?\n\n` +
                `The camera will be offline for approximately 60 seconds.\n` +
                `All streams from this camera will be interrupted.`
            );

            if (!confirmed) {
                console.log(`[Reboot] ${cameraId}: Cancelled by user`);
                return;
            }

            console.log(`[Reboot] ${cameraId}: Initiating camera reboot...`);
            this.setStreamStatus($streamItem, 'loading', 'Rebooting camera...');

            try {
                const response = await fetch(`/api/camera/${cameraId}/reboot`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ confirm: 'REBOOT' })
                });

                const result = await response.json();

                if (result.success) {
                    console.log(`[Reboot] ${cameraId}: ${result.message}`);
                    this.setStreamStatus($streamItem, 'inactive', 'Camera rebooting...');

                    // Show feedback to user
                    alert(`Reboot command sent to ${cameraName}.\n\n${result.message}`);
                } else {
                    throw new Error(result.error || 'Unknown error');
                }

            } catch (error) {
                console.error(`[Reboot] ${cameraId}: Failed - ${error.message}`);
                this.setStreamStatus($streamItem, 'error', `Reboot failed: ${error.message}`);
                alert(`Failed to reboot ${cameraName}:\n${error.message}`);
            }
        });

        // ESC key to exit CSS fullscreen or expanded modal
        $(document).on('keydown', (e) => {
            if (e.key === 'Escape') {
                // First check fullscreen (higher priority)
                const $fullscreenItem = $('.stream-item.css-fullscreen');
                if ($fullscreenItem.length > 0) {
                    console.log('[Fullscreen] ESC key pressed, exiting fullscreen');
                    // Force immediate UI exit in case closeFullscreen is stuck
                    this.forceExitFullscreen();
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

        // Global emergency exit - triple-click anywhere to force exit fullscreen
        // Useful when stream is frozen and button/ESC not responding
        let tripleClickTimer = null;
        let clickCount = 0;
        $(document).on('click', () => {
            clickCount++;
            if (clickCount >= 3) {
                const $fullscreenItem = $('.stream-item.css-fullscreen');
                if ($fullscreenItem.length > 0) {
                    console.log('[Fullscreen] Emergency triple-click exit');
                    this.forceExitFullscreen();
                }
                clickCount = 0;
            }
            clearTimeout(tripleClickTimer);
            tripleClickTimer = setTimeout(() => { clickCount = 0; }, 500);
        });

        // ============================================================================
        // EXPANDED MODAL MODE HANDLERS
        // Tap/click on camera card to expand to larger modal view
        // ============================================================================

        // Click on stream item (not buttons) to expand
        this.$container.on('click', '.stream-item', async (e) => {
            // Don't expand if arrange mode is active — let SortableJS handle the interaction
            if (this.$container.hasClass('arrange-mode')) {
                return;
            }

            // Don't expand if clicking on a button, interactive element, or the more menu
            if ($(e.target).closest('button, .ptz-controls, .stream-controls, .stream-more-menu, a, input, select').length) {
                return;
            }

            const $streamItem = $(e.currentTarget);

            // Don't toggle expand/collapse while PTZ is active — rapid PTZ clicks
            // would otherwise cause accidental modal exit
            if ($streamItem.find('.stream-ptz-toggle-btn').hasClass('ptz-active')) {
                return;
            }

            // Don't expand if already in fullscreen
            if ($streamItem.hasClass('css-fullscreen')) {
                return;
            }

            // Toggle expanded state
            if ($streamItem.hasClass('expanded')) {
                // Block collapse if this camera is pinned — must unpin first
                const serial = $streamItem.data('camera-serial');
                if (serial && serial === localStorage.getItem('pinnedCamera')) {
                    console.log(`[Expanded] Collapse blocked — camera ${serial} is pinned`);
                    return;
                }
                this.collapseExpandedCamera();
            } else {
                await this.expandCamera($streamItem);
            }
        });

        // Click backdrop to collapse expanded camera — blocked if current camera is pinned
        $('#expanded-backdrop').on('click', () => {
            const $expanded = $('.stream-item.expanded');
            const serial = $expanded.data('camera-serial');
            if (serial && serial === localStorage.getItem('pinnedCamera')) {
                // Pinned camera: backdrop click does nothing; must unpin first
                console.log(`[Expanded] Backdrop blocked — camera ${serial} is pinned`);
                return;
            }
            this.collapseExpandedCamera();
        });

        // HD toggle button (visible in expanded + fullscreen views)
        // Toggles between main (HD) and sub (SD) quality using the existing
        // camera-selector:quality-change event pipeline.
        this.$container.on('click', '.stream-hd-btn', async (e) => {
            e.stopPropagation();
            const $btn = $(e.currentTarget);
            const $streamItem = $btn.closest('.stream-item');
            const serial = $streamItem.data('camera-serial');
            const isHD = $btn.hasClass('hd-active');
            const newQuality = isHD ? 'sub' : 'main';

            // Toggle button visual state
            $btn.toggleClass('hd-active', !isHD);
            $btn.find('.hd-btn-label').text(!isHD ? 'HD' : 'SD');

            // Persist to localStorage (same key as camera-selector-controller)
            try {
                const stored = localStorage.getItem('hdCameras');
                let hdList = stored ? JSON.parse(stored) : [];
                if (!isHD) {
                    if (!hdList.includes(serial)) hdList.push(serial);
                } else {
                    hdList = hdList.filter(s => s !== serial);
                }
                localStorage.setItem('hdCameras', JSON.stringify(hdList));

                // Persist to DB via /api/my-preferences
                fetch('/api/my-preferences', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ hd_cameras: hdList })
                }).catch(err => console.warn('[HD] Failed to save HD preference:', err));
            } catch (err) {
                console.warn('[HD] localStorage error:', err);
            }

            // Fire quality-change event — stream.js handles the actual restart
            $streamItem.trigger('camera-selector:quality-change', { quality: newQuality });
            console.log(`[HD] ${serial}: switched to ${newQuality}`);

            // Floating window activation: if camera is also pinned, HD on = float, HD off = return to grid
            const isPinned = localStorage.getItem('pinnedCamera') === serial;
            if (isPinned) {
                if (!isHD) {
                    // Switched TO HD while pinned → activate floating window
                    pinnedWindowManager.activate(serial, $streamItem);
                } else if (pinnedWindowManager.isActive(serial)) {
                    // Switched BACK to SD while floating → return to grid then expand as normal modal
                    pinnedWindowManager.deactivate(serial);
                    await this.expandCamera($streamItem);
                }
            }
        });

        // Pin toggle button (visible in expanded + fullscreen views)
        // Persists as localStorage 'pinnedCamera' + DB 'pinned_camera'.
        // When pinned: camera auto-expands on reload, backdrop click blocked.
        this.$container.on('click', '.stream-pin-btn', async (e) => {
            e.stopPropagation();
            const $btn = $(e.currentTarget);
            const $streamItem = $btn.closest('.stream-item');
            const serial = $streamItem.data('camera-serial');
            const isPinned = $btn.hasClass('pin-active');

            if (isPinned) {
                // Unpin
                $btn.removeClass('pin-active');
                $btn.attr('title', 'Pin: keep this camera in expanded view across reloads');
                localStorage.removeItem('pinnedCamera');
                this._savePinnedCamera(null);
                // Deactivate floating window if active; return to normal expanded modal
                if (pinnedWindowManager.isActive(serial)) {
                    pinnedWindowManager.deactivate(serial);
                    await this.expandCamera($streamItem);
                }
                // Lift pinned lock + reload background streams
                $('#expanded-backdrop').removeClass('pinned-lock');
                this._reloadBackgroundStreamsAfterPin(serial);
                console.log(`[Pin] ${serial}: unpinned`);
            } else {
                // Unpin any previously pinned camera first
                this.$container.find('.stream-pin-btn.pin-active').removeClass('pin-active');
                // Pin this one
                $btn.addClass('pin-active');
                $btn.attr('title', 'Pinned — click to unpin (backdrop click disabled while pinned)');
                localStorage.setItem('pinnedCamera', serial);
                this._savePinnedCamera(serial);
                // Pinned lock: black backdrop + pause background streams
                if ($streamItem.hasClass('expanded')) {
                    $('#expanded-backdrop').addClass('pinned-lock');
                    this._pauseBackgroundStreamsForPin(serial);
                }
                // If camera is already HD, activate floating window immediately
                const isHD = $streamItem.find('.stream-hd-btn').hasClass('hd-active');
                if (isHD) {
                    pinnedWindowManager.activate(serial, $streamItem);
                }
                console.log(`[Pin] ${serial}: pinned`);
            }
        });

        // ============================================================================
        // PINNED WINDOW CLOSE EVENT
        // Fired by PinnedWindowManager when the × button is clicked on a floating window.
        // Handles unpin + SD quality switch on the stream.js side.
        // ============================================================================

        $(document).on('pinned-window:close', async (e, { serial }) => {
            console.log(`[PinnedWindow] Close event received for ${serial}`);

            // Clear pin state
            localStorage.removeItem('pinnedCamera');
            this._savePinnedCamera(null);

            const $tile = this.$container.find(`.stream-item[data-camera-serial="${serial}"]`);
            $tile.find('.stream-pin-btn')
                .removeClass('pin-active')
                .attr('title', 'Pin: keep this camera in expanded view across reloads');

            // Switch to SD directly (avoid re-triggering pin check in HD click handler)
            const $hdBtn = $tile.find('.stream-hd-btn');
            if ($hdBtn.hasClass('hd-active')) {
                $hdBtn.removeClass('hd-active').find('.hd-btn-label').text('SD');
                try {
                    const stored = localStorage.getItem('hdCameras');
                    let hdList = stored ? JSON.parse(stored) : [];
                    hdList = hdList.filter(s => s !== serial);
                    localStorage.setItem('hdCameras', JSON.stringify(hdList));
                    fetch('/api/my-preferences', {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ hd_cameras: hdList })
                    }).catch(() => {});
                } catch { /* non-critical */ }
                $tile.trigger('camera-selector:quality-change', { quality: 'sub' });
            }
        });

        // ============================================================================
        // MOBILE HAMBURGER MENU HANDLERS
        // The more button toggles a slide-up menu with all controls.
        // Each menu item proxies the click to the corresponding real button.
        // ============================================================================

        // Toggle the more menu
        this.$container.on('click', '.stream-more-btn', (e) => {
            e.stopPropagation();
            const $streamItem = $(e.currentTarget).closest('.stream-item');
            const $menu = $streamItem.find('.stream-more-menu');
            $menu.toggleClass('menu-visible');

            // Sync menu item active states with real button states
            if ($menu.hasClass('menu-visible')) {
                this._syncMoreMenuStates($streamItem);
            }
        });

        // Menu item click - proxy to the real button action
        this.$container.on('click', '.more-menu-item', (e) => {
            e.stopPropagation();
            const $item = $(e.currentTarget);
            const action = $item.data('action');
            const $streamItem = $item.closest('.stream-item');

            switch (action) {
                case 'audio':
                    $streamItem.find('.stream-audio-btn').trigger('click');
                    break;
                case 'ptz':
                    $streamItem.find('.stream-ptz-toggle-btn').trigger('click');
                    break;
                case 'controls':
                    $streamItem.find('.stream-controls-toggle-btn').trigger('click');
                    break;
                case 'settings':
                    $streamItem.find('.camera-settings-btn').trigger('click');
                    break;
                case 'record':
                    $streamItem.find('.camera-record-btn').trigger('click');
                    break;
                case 'playback':
                    $streamItem.find('.camera-playback-btn').trigger('click');
                    break;
                case 'power':
                    $streamItem.find('.stream-power-btn').trigger('click');
                    break;
                case 'talkback':
                    $streamItem.find('.stream-talkback-btn').trigger('click');
                    break;
                case 'fullscreen': {
                    // Enter fullscreen directly (don't trigger the btn which now
                    // closes the expanded modal). Close menu first.
                    $streamItem.find('.stream-more-menu').removeClass('menu-visible');
                    const cameraId = $streamItem.data('camera-serial');
                    const name = $streamItem.data('camera-name');
                    const cameraType = $streamItem.data('camera-type');
                    const streamType = $streamItem.data('stream-type');
                    this.openFullscreen(cameraId, name, cameraType, streamType);
                    break;
                }
            }

            // Close the menu after action (except for toggles that stay open)
            if (!['ptz', 'controls'].includes(action)) {
                $streamItem.find('.stream-more-menu').removeClass('menu-visible');
            } else {
                // Re-sync states after toggle
                setTimeout(() => this._syncMoreMenuStates($streamItem), 100);
            }
        });

        // Close more menu when tapping outside it (on the video area)
        this.$container.on('click', '.stream-video', (e) => {
            const $streamItem = $(e.target).closest('.stream-item');
            const $menu = $streamItem.find('.stream-more-menu.menu-visible');
            if ($menu.length) {
                $menu.removeClass('menu-visible');
            }
        });

        // Audio button click - toggle volume popup visibility
        this.$container.on('click', '.stream-audio-btn', (e) => {
            e.stopPropagation();
            const $button = $(e.currentTarget);
            const $streamItem = $button.closest('.stream-item');
            const $popup = $streamItem.find('.volume-popup');
            const $video = $streamItem.find('.stream-video');
            const videoEl = $video[0];
            const cameraId = $streamItem.data('camera-serial');

            if (!videoEl || videoEl.tagName !== 'VIDEO') {
                console.log('[Audio] Not a video element, cannot show volume popup');
                return;
            }

            // Close any other open popups first
            this.$container.find('.volume-popup').not($popup).hide();

            // Toggle this popup
            $popup.toggle();

            if ($popup.is(':visible')) {
                // Load current values into popup - use ACTUAL video state, not just stored prefs
                const pref = this.getAudioPreference(cameraId);
                const currentVolume = Math.round(videoEl.volume * 100);
                const currentMuted = videoEl.muted;

                // Use stored volume if available, but actual muted state from video element
                $popup.find('.volume-slider').val(pref.volume || currentVolume);
                $popup.find('.volume-value').text(`${pref.volume || currentVolume}%`);
                this.updateMuteButtonIcon($popup, currentMuted);
                console.log(`[Audio] ${cameraId}: Volume popup opened (volume=${pref.volume}%, muted=${currentMuted})`);
            }
        });

        // Volume slider input - real-time feedback while dragging
        this.$container.on('input', '.volume-slider', (e) => {
            const $slider = $(e.target);
            const $popup = $slider.closest('.volume-popup');
            const $streamItem = $popup.closest('.stream-item');
            const volume = parseInt($slider.val(), 10);

            // Update display
            $popup.find('.volume-value').text(`${volume}%`);

            // Apply immediately to video
            const videoEl = $streamItem.find('.stream-video')[0];
            if (videoEl) {
                videoEl.volume = volume / 100;

                // If volume > 0 and was muted, unmute automatically
                if (volume > 0 && videoEl.muted) {
                    videoEl.muted = false;
                }
                // If volume is 0, mute
                if (volume === 0) {
                    videoEl.muted = true;
                }

                // Always sync icons with actual muted state
                this.updateMuteButtonIcon($popup, videoEl.muted);
                this.updateAudioButtonIcon($streamItem, videoEl.muted);
            }
        });

        // Volume slider change - persist when user releases slider
        this.$container.on('change', '.volume-slider', (e) => {
            const $slider = $(e.target);
            const $popup = $slider.closest('.volume-popup');
            const $streamItem = $popup.closest('.stream-item');
            const cameraId = $streamItem.data('camera-serial');
            const volume = parseInt($slider.val(), 10);
            const videoEl = $streamItem.find('.stream-video')[0];
            const muted = videoEl ? videoEl.muted : true;

            this.saveAudioPreference(cameraId, volume, muted);
            console.log(`[Audio] ${cameraId}: Volume saved (volume=${volume}%, muted=${muted})`);
        });

        // Mute button in popup
        this.$container.on('click', '.volume-mute-btn', (e) => {
            e.stopPropagation();
            const $btn = $(e.currentTarget);
            const $popup = $btn.closest('.volume-popup');
            const $streamItem = $popup.closest('.stream-item');
            const cameraId = $streamItem.data('camera-serial');
            const videoEl = $streamItem.find('.stream-video')[0];

            if (!videoEl) return;

            // Toggle muted state
            videoEl.muted = !videoEl.muted;
            this.updateMuteButtonIcon($popup, videoEl.muted);
            this.updateAudioButtonIcon($streamItem, videoEl.muted);

            // Save preference
            const pref = this.getAudioPreference(cameraId);
            this.saveAudioPreference(cameraId, pref.volume, videoEl.muted);
            console.log(`[Audio] ${cameraId}: ${videoEl.muted ? 'Muted' : 'Unmuted'}`);
        });

        // Close volume popup when clicking outside
        $(document).on('click', (e) => {
            if (!$(e.target).closest('.volume-popup, .stream-audio-btn').length) {
                this.$container.find('.volume-popup').hide();
            }
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

                // MUTUAL EXCLUSIVITY: Hide stream controls when PTZ is shown
                const $streamControls = $streamItem.find('.stream-controls');
                const $streamToggle = $streamItem.find('.stream-controls-toggle-btn');
                if ($streamControls.hasClass('stream-controls-visible')) {
                    $streamControls.removeClass('stream-controls-visible');
                    $streamToggle.removeClass('controls-active');
                    console.log(`[PTZ] ${cameraId}: Stream controls hidden (mutual exclusivity)`);
                }
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

                // MUTUAL EXCLUSIVITY: Hide PTZ controls when stream controls are shown
                const $ptzControls = $streamItem.find('.ptz-controls');
                const $ptzToggle = $streamItem.find('.stream-ptz-toggle-btn');
                if ($ptzControls.hasClass('ptz-visible')) {
                    $ptzControls.removeClass('ptz-visible');
                    $ptzToggle.removeClass('ptz-active');
                    console.log(`[StreamControls] ${cameraId}: PTZ controls hidden (mutual exclusivity)`);
                }
            }

            // Save preference to localStorage
            this.saveStreamControlsPreference(cameraId, !isVisible);
        });

        // =====================================================================
        // Two-Way Audio (Talkback) Toggle Handlers
        // =====================================================================
        // TOGGLE behavior: click to start, click again to stop (or 10-min auto-timeout)
        // Only active for Eufy cameras (button only rendered for type='eufy')

        // Track talkback timeout timer
        let talkbackTimeoutId = null;
        const TALKBACK_TIMEOUT_MS = 10 * 60 * 1000; // 10 minutes

        // Toggle talkback on click
        this.$container.on('click', '.stream-talkback-btn', async (e) => {
            e.preventDefault();
            e.stopPropagation();

            const $button = $(e.currentTarget);
            const cameraId = $button.data('camera-id');
            const $streamItem = $button.closest('.stream-item');
            const cameraName = $streamItem.data('camera-name') || cameraId;

            // If permission denied, don't proceed
            if ($button.hasClass('talkback-denied')) {
                return;
            }

            // If already active, stop talkback (toggle off)
            if ($button.hasClass('talkback-active')) {
                console.log(`[Talkback] Toggle OFF for ${cameraId}`);

                // Clear timeout
                if (talkbackTimeoutId) {
                    clearTimeout(talkbackTimeoutId);
                    talkbackTimeoutId = null;
                }

                // Stop talkback
                talkbackManager.stopTalkback();

                // Update UI
                $button.removeClass('talkback-active talkback-connecting');
                $streamItem.removeClass('talkback-active');
                $button.removeAttr('data-talkback-status');
                return;
            }

            // If connecting, ignore click
            if ($button.hasClass('talkback-connecting')) {
                return;
            }

            console.log(`[Talkback] Toggle ON for ${cameraId} (${cameraName})`);

            // Show connecting state (button shows connecting while modal shows funny message)
            $button.addClass('talkback-connecting');

            try {
                // Start talkback (shows waiting modal, handles P2P, permission, WebSocket)
                const success = await talkbackManager.startTalkback(cameraId, cameraName);

                if (success) {
                    // Update UI to show active state
                    $button.removeClass('talkback-connecting');
                    $button.addClass('talkback-active');
                    $streamItem.addClass('talkback-active');
                    $button.attr('data-talkback-status', 'Talking... (click to stop)');
                    console.log(`[Talkback] Started for ${cameraId}`);

                    // Set auto-timeout (10 minutes)
                    talkbackTimeoutId = setTimeout(() => {
                        console.log(`[Talkback] Auto-timeout after 10 minutes for ${cameraId}`);
                        talkbackManager.stopTalkback();
                        $button.removeClass('talkback-active talkback-connecting');
                        $streamItem.removeClass('talkback-active');
                        $button.removeAttr('data-talkback-status');
                        talkbackTimeoutId = null;
                    }, TALKBACK_TIMEOUT_MS);

                } else {
                    // Failed to start
                    $button.removeClass('talkback-connecting');
                    $button.addClass('talkback-error');
                    $button.attr('data-talkback-status', 'Failed to start');
                    console.error(`[Talkback] Failed to start for ${cameraId}`);

                    // Clear error state after 3 seconds
                    setTimeout(() => {
                        $button.removeClass('talkback-error');
                        $button.removeAttr('data-talkback-status');
                    }, 3000);
                }
            } catch (error) {
                console.error(`[Talkback] Error starting:`, error);
                $button.removeClass('talkback-connecting');
                $button.addClass('talkback-error');
                $button.attr('data-talkback-status', error.message || 'Error');
            }
        });

        // Handle talkback manager state changes for UI updates
        talkbackManager.onStateChange((state) => {
            console.log(`[Talkback] State changed: ${state}`);
        });

        // Handle talkback errors
        talkbackManager.onError((error) => {
            console.error(`[Talkback] Error: ${error}`);

            // Check if it's a permission error
            if (error.includes('denied') || error.includes('Microphone')) {
                // Mark all talkback buttons as denied
                this.$container.find('.stream-talkback-btn').each((_, btn) => {
                    const $btn = $(btn);
                    $btn.addClass('talkback-denied');
                    $btn.attr('data-talkback-status', 'Microphone denied');
                });
            }
        });

        // Setup camera selector event handlers for show/hide and HD/SD quality changes
        this.setupCameraSelectorHandlers();

        // Stream type selector: handle option click in .stream-type-row — live switch
        this.$container.on('click.streamtype-option', '.stream-type-option', async (e) => {
            e.stopPropagation();
            const $option = $(e.target);
            const newType = $option.data('type');
            const $row = $option.closest('.stream-type-row');
            const cameraSerial = $row.data('camera-serial');

            // Update active state immediately for responsiveness
            $row.find('.stream-type-option').removeClass('active');
            $option.addClass('active');

            // Perform live switch (stops old, starts new, saves to DB)
            await this.switchStreamType(cameraSerial, newType);
        });

        // When stream controls panel opens, mark the current stream type as active
        this.$container.on('click.streamcontrols-type-sync', '.stream-controls-toggle-btn', (e) => {
            const $streamItem = $(e.target).closest('.stream-item');
            const currentType = $streamItem.data('stream-type');
            const $row = $streamItem.find('.stream-type-row');
            $row.find('.stream-type-option').removeClass('active');
            $row.find(`.stream-type-option[data-type="${currentType}"]`).addClass('active');
        });
    }

    /**
     * Reset all control states on page load.
     *
     * This ensures a clean state on reload, preventing cached/stale states
     * from persisting due to browser cache weirdness. Clears:
     * - PTZ panel visibility
     * - Stream controls visibility
     * - Audio unmute state
     * - Talkback button state
     *
     * Called early in init() before any streams are loaded.
     */
    resetAllControlStates() {
        console.log('[Init] Resetting all control states to default...');

        // Clear localStorage preferences that persist control states
        try {
            localStorage.removeItem('cameraAudioPreferences');
            localStorage.removeItem('cameraPTZPreferences');
            localStorage.removeItem('cameraStreamControlsPreferences');
            console.log('[Init] Cleared control preferences from localStorage');
        } catch (e) {
            console.warn('[Init] Failed to clear localStorage preferences:', e);
        }

        // Reset all PTZ panels to hidden
        this.$container.find('.ptz-controls').removeClass('ptz-visible');
        this.$container.find('.stream-ptz-toggle-btn').removeClass('ptz-active');

        // Reset all stream controls to hidden
        this.$container.find('.stream-controls').removeClass('controls-visible');
        this.$container.find('.stream-controls-toggle-btn').removeClass('controls-active');

        // Reset all audio buttons to muted state
        this.$container.find('.stream-audio-btn').removeClass('audio-active');
        this.$container.find('.stream-audio-btn i').removeClass('fa-volume-up').addClass('fa-volume-mute');

        // Mute all video elements
        this.$container.find('video').each(function() {
            this.muted = true;
        });

        // Reset talkback buttons
        this.$container.find('.stream-talkback-btn').removeClass('active');

        console.log('[Init] All control states reset to defaults');
    }

    /**
     * Save audio preference to localStorage.
     * Stores both volume (0-100) and muted state.
     *
     * @param {string} cameraId - Camera serial number
     * @param {number} volume - Volume level 0-100
     * @param {boolean} muted - Whether audio is muted
     */
    saveAudioPreference(cameraId, volume, muted) {
        try {
            const prefs = JSON.parse(localStorage.getItem('cameraAudioPreferences') || '{}');
            prefs[cameraId] = { volume, muted };
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
     * Get audio preference from localStorage.
     * Returns object with volume and muted state.
     * Handles legacy boolean format for backwards compatibility.
     *
     * @param {string} cameraId - Camera serial number
     * @returns {{ volume: number, muted: boolean }} - Audio preferences
     */
    getAudioPreference(cameraId) {
        try {
            const prefs = JSON.parse(localStorage.getItem('cameraAudioPreferences') || '{}');
            const pref = prefs[cameraId];

            // Handle legacy boolean format (true = unmuted, false = muted)
            if (typeof pref === 'boolean') {
                return { volume: 100, muted: !pref };
            }

            // Return stored preference or default (100% volume, muted)
            return pref || { volume: 100, muted: true };
        } catch (e) {
            return { volume: 100, muted: true };
        }
    }

    /**
     * Update the mute button icon inside the volume popup.
     *
     * @param {jQuery} $popup - The volume popup element
     * @param {boolean} muted - Whether audio is muted
     */
    updateMuteButtonIcon($popup, muted) {
        const $icon = $popup.find('.volume-mute-btn i');
        const $btn = $popup.find('.volume-mute-btn');
        if (muted) {
            $icon.removeClass('fa-volume-up fa-volume-down').addClass('fa-volume-mute');
            $btn.addClass('muted');
        } else {
            $icon.removeClass('fa-volume-mute').addClass('fa-volume-up');
            $btn.removeClass('muted');
        }
    }

    /**
     * Update the main audio button icon in the stream controls.
     *
     * @param {jQuery} $streamItem - The stream item container
     * @param {boolean} muted - Whether audio is muted
     */
    updateAudioButtonIcon($streamItem, muted) {
        const $button = $streamItem.find('.stream-audio-btn');
        const $icon = $button.find('i');
        if (muted) {
            $icon.removeClass('fa-volume-up').addClass('fa-volume-mute');
            $button.removeClass('audio-active');
        } else {
            $icon.removeClass('fa-volume-mute').addClass('fa-volume-up');
            $button.addClass('audio-active');
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
     * Apply saved audio preference to a video element.
     * Restores both volume level and muted state from localStorage.
     *
     * @param {string} cameraId - Camera serial number
     * @param {jQuery} $streamItem - The stream item container
     */
    applyAudioPreference(cameraId, $streamItem) {
        const pref = this.getAudioPreference(cameraId);
        const $video = $streamItem.find('.stream-video');
        const videoEl = $video[0];

        if (!videoEl || videoEl.tagName !== 'VIDEO') return;

        // Apply volume (0-100 -> 0.0-1.0) and muted state
        videoEl.volume = pref.volume / 100;
        videoEl.muted = pref.muted;

        // Update main audio button icon
        this.updateAudioButtonIcon($streamItem, pref.muted);

        console.log(`[Audio] ${cameraId}: Restored volume=${pref.volume}%, muted=${pref.muted}`);
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

            // Skip cameras that have native MJPEG endpoints or are configured as MJPEG
            // These cameras are completely isolated from MediaMTX/RTSP paths
            const hasNativeMJPEG = ['reolink', 'unifi', 'amcrest', 'sv3c'].includes(cameraType);
            const isConfiguredMJPEG = streamType === 'MJPEG';
            // NEOLINK cameras don't have native MJPEG even though they're Reolink
            const isNeolink = streamType === 'NEOLINK' || streamType === 'NEOLINK_LL_HLS';

            // Only add to mediaserver list if:
            // 1. NOT configured as MJPEG (stream_type !== 'MJPEG')
            // 2. AND (doesn't have native MJPEG OR is NEOLINK)
            if (!isConfiguredMJPEG && (!hasNativeMJPEG || isNeolink)) {
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

        // Get hidden cameras from localStorage (set by camera selector)
        const hiddenCameras = this._getHiddenCameras();
        const hdCameras = this._getHDCameras();
        console.log(`[StartAll] Hidden cameras: ${hiddenCameras.length}, HD cameras: ${hdCameras.length}`);

        // Sequential loading with delays to prevent resource exhaustion
        // This applies to all UIs (desktop, mobile, iOS) for consistent behavior
        let startedCount = 0;
        for (let index = 0; index < $streamItems.length; index++) {
            const $item = $($streamItems[index]);
            const cameraId = $item.data('camera-serial');
            const cameraType = $item.data('camera-type');
            const streamType = $item.data('stream-type');

            // Skip hidden cameras (filtered by camera selector)
            if (hiddenCameras.includes(cameraId)) {
                console.log(`[StartAll] Skipping hidden camera: ${cameraId}`);
                $item.hide();
                continue;
            }

            // Check if this camera should be in HD mode
            const isHD = hdCameras.includes(cameraId);
            if (isHD) {
                $item.addClass('hd-mode');
            }

            console.log(`[StartAll] Starting stream ${startedCount + 1}: ${cameraId}${isHD ? ' (HD)' : ''}`);

            try {
                await this.startStream(cameraId, $item, cameraType, streamType, isHD ? 'main' : 'sub');
                console.log(`[StartAll] ✓ Stream started: ${cameraId}`);
                startedCount++;
            } catch (error) {
                console.error(`[StartAll] ✗ Stream failed: ${cameraId}`, error);
                this.setStreamStatus($item, 'error', 'Failed to load');
            }

            // Delay between stream starts to let browser initialize each video element
            // Skip delay after last stream
            if (index < $streamItems.length - 1) {
                await new Promise(r => setTimeout(r, delayMs));
            }
        }

        // Update grid layout based on visible cameras
        this._updateGridLayoutForVisibleCameras();

        console.log(`[StartAll] ✓✓✓ ${startedCount} STREAMS COMPLETE ✓✓✓`);
    }

    /**
     * Get hidden cameras from localStorage (set by camera selector)
     * @returns {string[]} Array of hidden camera serial numbers
     */
    _getHiddenCameras() {
        try {
            const stored = localStorage.getItem('hiddenCameras');
            return stored ? JSON.parse(stored) : [];
        } catch (e) {
            return [];
        }
    }

    /**
     * Get HD-enabled cameras from localStorage (set by camera selector)
     * @returns {string[]} Array of HD camera serial numbers
     */
    _getHDCameras() {
        try {
            const stored = localStorage.getItem('hdCameras');
            return stored ? JSON.parse(stored) : [];
        } catch (e) {
            return [];
        }
    }

    /**
     * Load user's per-camera stream type preferences from the database.
     * Overrides the data-stream-type attribute on each .stream-item
     * so that startAllStreams() uses the user's preferred type.
     * Falls back silently to cameras.json defaults if the API fails.
     */
    async loadUserStreamPreferences() {
        try {
            const response = await fetch('/api/user/stream-preferences');
            if (!response.ok) {
                console.warn('[Prefs] Failed to load stream preferences:', response.status);
                return;
            }

            const prefs = await response.json();
            if (!prefs || prefs.length === 0) {
                console.log('[Prefs] No user stream preferences saved');
                return;
            }

            // Build lookup map: camera_serial → preferred_stream_type
            const prefMap = {};
            for (const p of prefs) {
                prefMap[p.camera_serial] = p.preferred_stream_type;
            }

            // Override data-stream-type on each stream item that has a preference
            let overrideCount = 0;
            this.$container.find('.stream-item').each(function () {
                const $item = $(this);
                const serial = $item.data('camera-serial');
                if (prefMap[serial]) {
                    const oldType = $item.data('stream-type');
                    const newType = prefMap[serial];
                    if (oldType !== newType) {
                        $item.attr('data-stream-type', newType);
                        $item.data('stream-type', newType);
                        console.log(`[Prefs] ${serial}: ${oldType} → ${newType}`);
                        overrideCount++;
                    }
                }
            });

            console.log(`[Prefs] Applied ${overrideCount} stream type overrides from ${prefs.length} saved preferences`);
        } catch (err) {
            console.warn('[Prefs] Error loading stream preferences (using defaults):', err);
        }
    }

    /**
     * Switch a camera's stream type live (stop current, start new, save preference).
     * Handles element swaps (video ↔ img) when switching to/from MJPEG.
     *
     * @param {string} cameraSerial - Camera serial number
     * @param {string} newStreamType - New stream type (WEBRTC, HLS, LL_HLS, MJPEG, etc.)
     */
    async switchStreamType(cameraSerial, newStreamType) {
        const $streamItem = this.$container.find(`.stream-item[data-camera-serial="${cameraSerial}"]`);
        if ($streamItem.length === 0) {
            console.error(`[SwitchType] Camera ${cameraSerial} not found`);
            return;
        }

        const oldStreamType = $streamItem.data('stream-type');
        if (oldStreamType === newStreamType) {
            console.log(`[SwitchType] ${cameraSerial}: already ${newStreamType}, no change`);
            return;
        }

        console.log(`[SwitchType] ${cameraSerial}: ${oldStreamType} → ${newStreamType}`);

        // Phase 0a: Save preference to database FIRST so backend resolves correct type
        // This must happen before any backend calls that use get_effective_stream_type()
        try {
            const prefResp = await fetch(`/api/user/stream-preferences/${cameraSerial}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ preferred_stream_type: newStreamType })
            });
            if (!prefResp.ok) {
                console.warn(`[SwitchType] Failed to save preference early: ${prefResp.status}`);
            } else {
                console.log(`[SwitchType] Preference saved: ${newStreamType} for ${cameraSerial}`);
            }
        } catch (prefErr) {
            console.warn('[SwitchType] Error saving preference early:', prefErr);
        }

        // Phase 0b: Check MediaMTX path availability for non-MJPEG types
        // MJPEG connects directly to camera, all others need MediaMTX
        const mediamtxTypes = ['WEBRTC', 'HLS', 'LL_HLS', 'NEOLINK', 'NEOLINK_LL_HLS'];
        if (mediamtxTypes.includes(newStreamType)) {
            try {
                const checkResp = await fetch(`/api/mediamtx/path-status/${cameraSerial}`);
                if (checkResp.ok) {
                    const pathStatus = await checkResp.json();
                    if (!pathStatus.ready) {
                        // If switching FROM MJPEG, the path likely doesn't exist yet.
                        // MJPEG cameras bypass MediaMTX entirely, so no path was created at startup.
                        // Create one on demand and start the FFmpeg publisher.
                        if (oldStreamType === 'MJPEG') {
                            console.log(`[SwitchType] MJPEG → ${newStreamType}: creating MediaMTX path on demand`);
                            const cameraName = $streamItem.data('camera-name') || cameraSerial;
                            this._showStreamTypeToast(cameraName, 'Preparing stream path...', 'info');

                            try {
                                const createResp = await fetch(`/api/mediamtx/create-path/${cameraSerial}`, {
                                    method: 'POST',
                                    headers: { 'Content-Type': 'application/json' },
                                    body: JSON.stringify({ target_type: newStreamType })
                                });

                                if (!createResp.ok) {
                                    const err = await createResp.json().catch(() => ({}));
                                    throw new Error(err.error || `HTTP ${createResp.status}`);
                                }

                                const createResult = await createResp.json();
                                console.log(`[SwitchType] MediaMTX paths created:`, createResult.paths_created);

                                // Wait for FFmpeg to establish connection with MediaMTX
                                this._showStreamTypeToast(cameraName, 'Starting stream publisher...', 'info');
                                await new Promise(r => setTimeout(r, 5000));
                            } catch (createErr) {
                                console.error(`[SwitchType] Failed to create MediaMTX path:`, createErr);
                                const cameraName = $streamItem.data('camera-name') || cameraSerial;
                                this._showStreamTypeToast(cameraName,
                                    `Could not prepare stream: ${createErr.message}`, 'error');
                                const $row = $streamItem.find('.stream-type-row');
                                $row.find('.stream-type-option').removeClass('active');
                                $row.find(`.stream-type-option[data-type="${oldStreamType}"]`).addClass('active');
                                return;
                            }
                        } else {
                            // Not switching from MJPEG — path should exist but isn't ready
                            console.warn(`[SwitchType] MediaMTX path not ready for ${cameraSerial}: ${pathStatus.message}`);
                            const cameraName = $streamItem.data('camera-name') || cameraSerial;
                            this._showStreamTypeToast(cameraName, pathStatus.message, 'error');
                            const $row = $streamItem.find('.stream-type-row');
                            $row.find('.stream-type-option').removeClass('active');
                            $row.find(`.stream-type-option[data-type="${oldStreamType}"]`).addClass('active');
                            return;
                        }
                    }
                }
            } catch (err) {
                console.warn('[SwitchType] Could not check MediaMTX path, proceeding anyway:', err);
            }
        }

        // Phase 1: Stop current stream
        this.hlsManager.stopStream(cameraSerial);
        this.webrtcManager.stopStream(cameraSerial);
        if (this.mjpegManager) {
            this.mjpegManager.stopStream(cameraSerial);
        }

        // Phase 2: Handle element swap if switching to/from MJPEG
        let videoEl = $streamItem.find('video')[0];
        const imgEl = $streamItem.find('img.mjpeg-stream')[0];

        if (newStreamType === 'MJPEG') {
            // Switching TO MJPEG: need img element, hide video
            if (videoEl) videoEl.style.display = 'none';
            if (!imgEl) {
                const img = document.createElement('img');
                img.className = 'mjpeg-stream';
                img.style.width = '100%';
                img.style.height = '100%';
                img.style.objectFit = 'contain';
                $streamItem.find('.video-container').append(img);
            } else {
                imgEl.style.display = '';
            }
        } else {
            // Switching FROM MJPEG (or between video-based types): show video, hide img
            if (imgEl) imgEl.style.display = 'none';
            if (videoEl) videoEl.style.display = '';
        }

        // Phase 3: Update data attribute so all downstream code uses new type
        $streamItem.attr('data-stream-type', newStreamType);
        $streamItem.data('stream-type', newStreamType);

        // Phase 4: Start new stream
        const quality = $streamItem.hasClass('hd-mode') ? 'main' : 'sub';
        try {
            if (newStreamType === 'MJPEG') {
                const targetEl = $streamItem.find('img.mjpeg-stream')[0];
                if (this.mjpegManager && targetEl) {
                    await this.mjpegManager.startStream(cameraSerial, targetEl, quality);
                }
            } else if (newStreamType === 'WEBRTC') {
                videoEl = $streamItem.find('video')[0];
                if (videoEl) {
                    await this.webrtcManager.startStream(cameraSerial, videoEl, quality);
                }
            } else {
                // HLS, LL_HLS, NEOLINK, NEOLINK_LL_HLS — all go through HLS manager
                videoEl = $streamItem.find('video')[0];
                if (videoEl) {
                    await this.hlsManager.startStream(cameraSerial, videoEl, quality);
                }
            }

            this.setStreamStatus($streamItem, 'live', quality === 'main' ? 'HD' : 'Live');
            console.log(`[SwitchType] ${cameraSerial}: now streaming ${newStreamType}`);
        } catch (err) {
            console.error(`[SwitchType] ${cameraSerial}: failed to start ${newStreamType}:`, err);
            this.setStreamStatus($streamItem, 'error', 'Error');
        }

        // Phase 5: Preference already saved in Phase 0a (before stream operations)
    }

    /**
     * Show a toast notification for stream type operations.
     * @param {string} cameraName - Camera display name
     * @param {string} message - Message text
     * @param {string} type - 'error', 'success', or 'info'
     */
    _showStreamTypeToast(cameraName, message, type = 'info') {
        const iconClass = type === 'success' ? 'fa-check-circle' :
                         type === 'error' ? 'fa-exclamation-triangle' :
                         'fa-info-circle';
        const bgColor = type === 'success' ? 'rgba(46, 204, 113, 0.95)' :
                       type === 'error' ? 'rgba(231, 76, 60, 0.95)' :
                       'rgba(52, 152, 219, 0.95)';

        const $toast = $(`
            <div class="stream-type-toast" style="
                position: fixed; top: 80px; right: 20px;
                background: ${bgColor}; color: #fff;
                padding: 16px 20px; border-radius: 8px;
                box-shadow: 0 4px 12px rgba(0,0,0,0.3);
                z-index: 100000; display: flex; align-items: center;
                gap: 12px; max-width: 400px;
                animation: slideInRight 0.3s ease;
            ">
                <i class="fas ${iconClass}" style="font-size: 20px; flex-shrink: 0;"></i>
                <div>
                    <div style="font-weight: 600;">${cameraName}</div>
                    <div style="font-size: 13px; opacity: 0.9;">${message}</div>
                </div>
            </div>
        `);

        $('body').append($toast);
        setTimeout(() => $toast.fadeOut(400, () => $toast.remove()), 6000);
    }

    /**
     * Update grid layout based on currently visible cameras
     */
    _updateGridLayoutForVisibleCameras() {
        // Guard: never recalculate grid during fullscreen — only 1 item is visible
        // due to CSS :has() hiding, which would incorrectly set grid-1.
        if ($('.stream-item.css-fullscreen').length > 0) {
            console.log('[Layout] Skipping grid update (in fullscreen mode)');
            return;
        }
        const visibleCount = this.$container.find('.stream-item:visible').length;
        let cols;
        if (visibleCount === 0) cols = 1;
        else if (visibleCount === 1) cols = 1;
        else if (visibleCount <= 4) cols = 2;
        else if (visibleCount <= 9) cols = 3;
        else if (visibleCount <= 16) cols = 4;
        else cols = 5;

        this.$container
            .removeClass('grid-1 grid-2 grid-3 grid-4 grid-5')
            .addClass(`grid-${cols}`);

        console.log(`[Layout] Grid updated: ${cols} columns for ${visibleCount} visible cameras`);
    }

    async startStream(cameraId, $streamItem, cameraType, streamType, quality = 'sub') {
        let streamElement = $streamItem.find('.stream-video')[0];
        // Store the quality setting for this stream
        $streamItem.data('grid-quality', quality);
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

        // Check if this camera is marked for HD mode in the camera selector
        // HD cameras should use native streams, not snapshots/MJPEG
        const isHDMode = quality === 'main' || this._getHDCameras().includes(cameraId);

        // iOS in grid view: use snapshots (not MJPEG) unless:
        // - user forces WebRTC via settings
        // - camera is selected for HD mode (use native stream for HD)
        const useIOSSnapshot = isIOSDevice() && isGridView && !debugForceMJPEG && !isForceWebRTCGridEnabled() && !isHDMode;
        // Desktop users can opt-in to snapshot mode via Settings (but HD cameras use native)
        const useDesktopSnapshot = !isPortableDevice() && isGridSnapshotsEnabled() && isGridView && !isHDMode;
        // Android/other portable in grid: use MJPEG unless user forces WebRTC via settings
        const forcePortableMJPEG = (isPortableDevice() && !isIOSDevice() || debugForceMJPEG) && isGridView && !isHDMode && !isForceWebRTCGridEnabled();
        // Use snapshots for: iOS, desktop opt-in, or debug mode (but never for HD cameras)
        const useSnapshot = useIOSSnapshot || useDesktopSnapshot || (debugForceSnapshot && isGridView && !isHDMode);

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

        // Force WebRTC Grid Mode (for all mobile devices)
        // When enabled, portable devices use WebRTC in grid view instead of MJPEG/snapshots.
        // This provides real-time video but may cause issues with many cameras.
        const forceWebRTCGrid = isPortableDevice() && isGridView && isForceWebRTCGridEnabled();
        if (forceWebRTCGrid && streamType !== 'WEBRTC') {
            console.log(`[Stream] Force WebRTC Grid enabled - using WebRTC for ${cameraId}`);
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
                // Pass quality parameter for grid view (MJPEG only supports sub for now)
                // MJPEG typically only has one quality level
                success = await this.mjpegManager.startStream(cameraId, streamElement, cameraType, 'sub');
            } else if (streamType === 'HLS' || streamType === 'LL_HLS' || streamType === 'NEOLINK' || streamType === 'NEOLINK_LL_HLS') {
                // Use quality parameter (main or sub) - supports HD grid selection
                success = await this.hlsManager.startStream(cameraId, streamElement, quality);
                console.log(`[Stream] HLS started for ${cameraId} with quality: ${quality}`);
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
                        success = await this.webrtcManager.startStream(cameraId, streamElement, quality);
                    } else {
                        // No DTLS - fall back to HLS (iOS requires encryption)
                        console.log(`[Stream] iOS without DTLS - falling back to HLS for ${cameraId} (~2-4s latency)`);
                        success = await this.hlsManager.startStream(cameraId, streamElement, quality);
                        // Update the stream type on the element so fullscreen/recovery works correctly
                        $streamItem.data('stream-type', 'LL_HLS');
                    }
                } else {
                    // Non-iOS: WebRTC works without DTLS on LAN
                    success = await this.webrtcManager.startStream(cameraId, streamElement, quality);
                    console.log(`[Stream] WebRTC started for ${cameraId} with quality: ${quality}`);
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

    // =========================================================================
    // Stream Reload Overlay helpers
    //
    // These power the per-tile animation shown while the user-triggered refresh
    // or backend restart is in progress.  The overlay reuses the standby-overlay
    // visual language (concentric spinning rings) but is scoped to a single
    // stream-item via position:absolute.
    // =========================================================================

    /**
     * Show the reload overlay on a single stream tile.
     *
     * @param {jQuery} $streamItem - The .stream-item container
     * @param {'refresh'|'restart'} mode
     *   'refresh' = client-side HLS.js reconnect (fast, no backend call)
     *   'restart' = backend FFmpeg kill + respawn (slower, multi-step)
     */
    _showStreamReloadOverlay($streamItem, mode) {
        const labels = {
            refresh: {
                title:    'Refreshing Stream',
                subtitle: 'Client-side HLS.js reconnect'
            },
            restart: {
                title:    'Restarting Stream',
                subtitle: 'Backend FFmpeg process restart'
            }
        };
        const cfg = labels[mode] || labels.refresh;
        const $overlay = $streamItem.find('.stream-reload-overlay');
        $overlay.find('.sro-title').text(cfg.title);
        $overlay.find('.sro-subtitle').text(cfg.subtitle);
        $overlay.find('.sro-log').empty();
        $overlay.removeClass('waking').addClass('active');

        // Reset the message queue timestamp so the first log fires immediately.
        // Subsequent synchronous _logStreamReloadStep calls are staggered by
        // SRO_LOG_STEP_MS so the user can read each line as it appears.
        $streamItem[0]._sroNextLogAt = Date.now();
    }

    /**
     * Hide the reload overlay.
     *
     * When waking=true the overlay briefly switches to the green "waking" state
     * (rings accelerate, eye turns green) before fading out — mirroring how the
     * full-page standby overlay signals that streams are coming back online.
     *
     * @param {jQuery} $streamItem
     * @param {boolean} [waking=false] - Animate the "stream reconnected" transition
     */
    _hideStreamReloadOverlay($streamItem, waking = false) {
        const $overlay = $streamItem.find('.stream-reload-overlay');
        if (waking) {
            $overlay.addClass('waking');
            setTimeout(() => $overlay.removeClass('active waking'), 1200);
        } else {
            $overlay.removeClass('active waking');
        }
    }

    /**
     * Append a timestamped status line to the overlay log.
     *
     * Calls are queued so that even synchronous bursts of log messages appear
     * one at a time with a SRO_LOG_STEP_MS gap between them, giving the user
     * time to read each line.  The queue is reset by _showStreamReloadOverlay.
     *
     * Lines animate in (fade + slide) and the log auto-scrolls to the bottom.
     *
     * @param {jQuery} $streamItem
     * @param {string} message - Human-readable description of the current step
     * @param {'info'|'success'|'warn'|'error'} [type='info'] - Colour coding
     */
    _logStreamReloadStep($streamItem, message, type = 'info') {
        // Milliseconds between consecutive log lines — slow enough to read,
        // fast enough not to frustrate.
        const SRO_LOG_STEP_MS = 700;

        const el = $streamItem[0];
        const now = Date.now();

        // Calculate how far in the future this message should appear
        const fireAt = Math.max(now, el._sroNextLogAt ?? now);
        const delay  = fireAt - now;

        // Advance the queue pointer for the next caller
        el._sroNextLogAt = fireAt + SRO_LOG_STEP_MS;

        setTimeout(() => {
            const $log = $streamItem.find('.sro-log');
            const ts = new Date().toLocaleTimeString('en-US', {
                hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit'
            });
            const $line = $(`<div class="sro-log-line sro-log-${type}">` +
                `<span class="sro-ts">${ts}</span>` +
                `<span class="sro-text">${message}</span>` +
                `</div>`);
            $log.append($line);
            // Auto-scroll so the latest step is always visible
            const logEl = $log[0];
            if (logEl) logEl.scrollTop = logEl.scrollHeight;
        }, delay);
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

        // Handle "Signal Lost" overlay - show when stream fails/is loading, hide when live
        // Signal Lost is shown for error, failed, and extended loading states
        const showSignalLost = ['error', 'failed'].includes(status) ||
            (status === 'loading' && /retry|nuclear|reconnect/i.test(text));

        if (showSignalLost) {
            $streamItem.addClass('signal-lost');
        } else if (status === 'live') {
            $streamItem.removeClass('signal-lost');
        }

        // Check if quiet mode is enabled
        const quietMode = localStorage.getItem('quietStatusMessages') === 'true';

        if (quietMode) {
            // In quiet mode, only show important statuses
            // Important: Starting, Connecting, Live, Failed, Error, Stopped, Signal Lost
            // Verbose (hidden): Refreshing, Degraded, Recovered, Buffering, Retry, Restarting, Nuclear
            const importantPatterns = [
                /^Starting/i,
                /^Connecting/i,
                /^Live/i,
                /^Failed/i,
                /^Error/i,
                /^Stopped/i,
                /^Signal Lost/i,
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

    /**
     * Start fullscreen main stream with retry logic.
     * If main stream fails, starts sub stream but schedules retry attempts
     * with exponential backoff to re-attempt main stream.
     *
     * @param {string} cameraId - Camera serial number
     * @param {HTMLVideoElement} videoEl - Video element to attach stream to
     * @param {jQuery} $streamItem - Stream item jQuery element
     * @param {boolean} useWebRTC - Whether to use WebRTC (true) or HLS (false)
     * @returns {Promise<{success: boolean, streamType: string, quality: string}>}
     */
    async _startMainStreamWithRetry(cameraId, videoEl, $streamItem, useWebRTC) {
        const maxRetries = 3;
        const baseDelayMs = 2000;  // 2 seconds initial delay, doubles each retry

        // Clear any existing retry timer for this camera
        const existingTimer = $streamItem.data('main-stream-retry-timer');
        if (existingTimer) {
            clearTimeout(existingTimer);
            $streamItem.data('main-stream-retry-timer', null);
        }

        // Try to start main stream
        for (let attempt = 0; attempt <= maxRetries; attempt++) {
            try {
                if (attempt > 0) {
                    const delay = baseDelayMs * Math.pow(2, attempt - 1);
                    console.log(`[Fullscreen] ${cameraId}: Retry ${attempt}/${maxRetries} for main stream in ${delay}ms...`);
                    await new Promise(resolve => setTimeout(resolve, delay));

                    // Check if still in fullscreen mode before retry
                    if (!$streamItem.hasClass('css-fullscreen')) {
                        console.log(`[Fullscreen] ${cameraId}: No longer in fullscreen, aborting retry`);
                        return { success: false, streamType: 'none', quality: 'none' };
                    }
                }

                // Attempt main stream
                if (useWebRTC) {
                    const dtlsEnabled = await isDTLSEnabled();
                    if (dtlsEnabled || !isIOSDevice()) {
                        await this.webrtcManager.startStream(cameraId, videoEl, 'main');
                        console.log(`[Fullscreen] ${cameraId}: ✓ WebRTC main stream started (attempt ${attempt + 1})`);
                        $streamItem.data('fullscreen-stream-type', 'webrtc');
                        $streamItem.data('switched-to-main', true);
                        $streamItem.data('fullscreen-quality', 'main');
                        return { success: true, streamType: 'webrtc', quality: 'main' };
                    } else {
                        // iOS without DTLS - must use HLS
                        console.log(`[Fullscreen] ${cameraId}: iOS without DTLS - using HLS`);
                        await this.hlsManager.startStream(cameraId, videoEl, 'main');
                        $streamItem.data('fullscreen-stream-type', 'hls');
                        $streamItem.data('switched-to-main', true);
                        $streamItem.data('fullscreen-quality', 'main');
                        return { success: true, streamType: 'hls', quality: 'main' };
                    }
                } else {
                    await this.hlsManager.startStream(cameraId, videoEl, 'main');
                    console.log(`[Fullscreen] ${cameraId}: ✓ HLS main stream started (attempt ${attempt + 1})`);
                    $streamItem.data('fullscreen-stream-type', 'hls');
                    $streamItem.data('switched-to-main', true);
                    $streamItem.data('fullscreen-quality', 'main');
                    return { success: true, streamType: 'hls', quality: 'main' };
                }
            } catch (e) {
                console.warn(`[Fullscreen] ${cameraId}: Main stream attempt ${attempt + 1} failed:`, e.message);
                if (attempt === maxRetries) {
                    // All retries exhausted, fall back to sub stream
                    console.error(`[Fullscreen] ${cameraId}: All main stream attempts failed, falling back to sub`);
                    break;
                }
            }
        }

        // Fall back to sub stream
        try {
            await this.hlsManager.startStream(cameraId, videoEl, 'sub');
            console.log(`[Fullscreen] ${cameraId}: ✓ Fell back to HLS sub stream`);
            $streamItem.data('fullscreen-stream-type', 'hls');
            $streamItem.data('switched-to-main', false);
            $streamItem.data('fullscreen-quality', 'sub');

            // Schedule background retry to upgrade to main stream
            this._scheduleMainStreamUpgrade(cameraId, videoEl, $streamItem, useWebRTC);

            return { success: true, streamType: 'hls', quality: 'sub' };
        } catch (subError) {
            console.error(`[Fullscreen] ${cameraId}: Even sub stream failed:`, subError);
            return { success: false, streamType: 'none', quality: 'none' };
        }
    }

    /**
     * Schedule background retry to upgrade from sub to main stream.
     * Runs periodic retries with exponential backoff while in fullscreen mode.
     *
     * @param {string} cameraId - Camera serial number
     * @param {HTMLVideoElement} videoEl - Video element
     * @param {jQuery} $streamItem - Stream item element
     * @param {boolean} useWebRTC - Whether to use WebRTC
     */
    _scheduleMainStreamUpgrade(cameraId, videoEl, $streamItem, useWebRTC) {
        const retryDelays = [10000, 20000, 40000, 60000];  // 10s, 20s, 40s, 60s
        let retryIndex = 0;

        const attemptUpgrade = async () => {
            // Check if still in fullscreen and still on sub quality
            if (!$streamItem.hasClass('css-fullscreen')) {
                console.log(`[Fullscreen] ${cameraId}: No longer in fullscreen, stopping upgrade attempts`);
                return;
            }

            if ($streamItem.data('fullscreen-quality') === 'main') {
                console.log(`[Fullscreen] ${cameraId}: Already on main quality, stopping upgrade attempts`);
                return;
            }

            console.log(`[Fullscreen] ${cameraId}: Attempting upgrade to main stream...`);

            try {
                // Stop current sub stream
                this.hlsManager.stopStream(cameraId);

                // Try main stream
                if (useWebRTC) {
                    const dtlsEnabled = await isDTLSEnabled();
                    if (dtlsEnabled || !isIOSDevice()) {
                        await this.webrtcManager.startStream(cameraId, videoEl, 'main');
                        console.log(`[Fullscreen] ${cameraId}: ✓ Upgraded to WebRTC main stream`);
                        $streamItem.data('fullscreen-stream-type', 'webrtc');
                        $streamItem.data('fullscreen-quality', 'main');
                        $streamItem.data('switched-to-main', true);
                        return;  // Success - stop retrying
                    }
                }

                // HLS main
                await this.hlsManager.startStream(cameraId, videoEl, 'main');
                console.log(`[Fullscreen] ${cameraId}: ✓ Upgraded to HLS main stream`);
                $streamItem.data('fullscreen-stream-type', 'hls');
                $streamItem.data('fullscreen-quality', 'main');
                $streamItem.data('switched-to-main', true);

            } catch (e) {
                console.warn(`[Fullscreen] ${cameraId}: Upgrade to main failed, staying on sub:`, e.message);

                // Restart sub stream if it was stopped
                try {
                    await this.hlsManager.startStream(cameraId, videoEl, 'sub');
                } catch (subError) {
                    console.error(`[Fullscreen] ${cameraId}: Failed to restart sub stream:`, subError);
                }

                // Schedule next retry if we haven't exhausted all delays
                if (retryIndex < retryDelays.length - 1) {
                    retryIndex++;
                }
                const nextDelay = retryDelays[retryIndex];
                console.log(`[Fullscreen] ${cameraId}: Next upgrade attempt in ${nextDelay / 1000}s`);

                const timer = setTimeout(attemptUpgrade, nextDelay);
                $streamItem.data('main-stream-retry-timer', timer);
            }
        };

        // Start first upgrade attempt after initial delay
        const timer = setTimeout(attemptUpgrade, retryDelays[0]);
        $streamItem.data('main-stream-retry-timer', timer);
        console.log(`[Fullscreen] ${cameraId}: Scheduled main stream upgrade in ${retryDelays[0] / 1000}s`);
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

            // Arrange mode is grid-view only — exit it before entering fullscreen
            if (tileArrangeManager.arrangeMode) {
                tileArrangeManager.exitArrangeMode(false);
            }
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

                // Start fullscreen stream with retry logic
                const useWebRTC = isFullscreenWebRTCEnabled();
                await this._startMainStreamWithRetry(cameraId, videoEl, $streamItem, useWebRTC);
                $streamItem.data('switched-from-snapshot', true);
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

                // Start fullscreen stream with retry logic
                const useWebRTC = isFullscreenWebRTCEnabled();
                await this._startMainStreamWithRetry(cameraId, videoEl, $streamItem, useWebRTC);
                $streamItem.data('switched-from-mjpeg', true);
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

                // Start main stream with retry logic
                await this._startMainStreamWithRetry(cameraId, videoEl, $streamItem, useWebRTC);
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
        // CRITICAL: First, immediately exit fullscreen UI to restore grid view
        // This ensures the user can exit even if stream operations hang
        const $fullscreenItem = $('.stream-item.css-fullscreen');

        if ($fullscreenItem.length === 0) {
            console.log('[Fullscreen] No fullscreen stream found');
            // Reset processing flag in case it got stuck
            this._fullscreenProcessing = false;
            // Still resume paused streams — forceExitFullscreen() removes the css-fullscreen
            // class before calling us, so $fullscreenItem will be empty but pausedStreams
            // still needs to be processed.
            await this._resumePausedStreams();
            return;
        }

        // STEP 1: Immediate UI exit - happens synchronously, no async ops
        $fullscreenItem.removeClass('css-fullscreen');
        localStorage.removeItem('fullscreenCameraSerial');
        console.log('[Fullscreen] CSS fullscreen class removed - UI exited immediately');

        // STEP 1b: Restore grid layout immediately.
        // During fullscreen, all other stream items are display:none (via CSS :has()).
        // If _updateGridLayoutForVisibleCameras() ran during fullscreen (e.g. via
        // startAllStreams triggered by reconnect/recovery), it would have seen only
        // 1 visible camera and set grid-1. We must restore the correct column count
        // now that all cameras are visible again.
        this.setupLayout();

        // Reset processing flag early so button becomes responsive
        this._fullscreenProcessing = false;

        // STEP 2: Async cleanup with timeout protection
        // If any stream operation hangs, the UI is already restored
        try {
            console.log('[Fullscreen] Starting async cleanup...');

            const fullscreenCameraId = $fullscreenItem.data('camera-serial');

            // Clear any pending main stream retry/upgrade timer
            const retryTimer = $fullscreenItem.data('main-stream-retry-timer');
            if (retryTimer) {
                clearTimeout(retryTimer);
                $fullscreenItem.data('main-stream-retry-timer', null);
                console.log(`[Fullscreen] Cleared main stream retry timer for ${fullscreenCameraId}`);
            }

            const switchedToMain = $fullscreenItem.data('switched-to-main');
            const switchedFromMJPEG = $fullscreenItem.data('switched-from-mjpeg');
            const switchedFromSnapshot = $fullscreenItem.data('switched-from-snapshot');
            const originalStreamType = $fullscreenItem.data('original-stream-type');
            const streamType = $fullscreenItem.data('stream-type');
            const cameraType = $fullscreenItem.data('camera-type');

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
                $fullscreenItem.removeData('fullscreen-quality');
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
                $fullscreenItem.removeData('fullscreen-quality');
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
                $fullscreenItem.removeData('fullscreen-quality');
            }

            await this._resumePausedStreams();

        } catch (error) {
            console.error('[Fullscreen] Error closing fullscreen:', error);
        }
    }

    /**
     * Resume all streams that were paused when entering fullscreen.
     *
     * Extracted from closeFullscreen() so it can be called independently by
     * forceExitFullscreen(), which removes the css-fullscreen class before
     * invoking closeFullscreen() — causing closeFullscreen() to take the early
     * return path (no .css-fullscreen item found) and skip resume without this helper.
     */
    async _resumePausedStreams() {
        if (!this.pausedStreams || this.pausedStreams.length === 0) return;

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
                    hls.startLoad();
                    videoEl.play().catch(e => console.log(`[Fullscreen] Play blocked for ${stream.id}:`, e));
                    this.attachHealthMonitor(stream.id, $item, itemStreamType);
                }
            }
            else if (stream.type === 'RTMP') {
                if (videoEl) {
                    console.log(`[Fullscreen] Resuming RTMP stream: ${stream.id}`);
                    videoEl.play().catch(e => console.log(`[Fullscreen] Play blocked for ${stream.id}:`, e));
                    this.attachHealthMonitor(stream.id, $item, itemStreamType);
                }
            }
            else if (stream.type === 'MJPEG') {
                const imgEl = $video[0];
                if (imgEl && imgEl._pausedSrc) {
                    console.log(`[Fullscreen] Resuming MJPEG stream: ${stream.id}`);
                    imgEl.src = imgEl._pausedSrc;
                    delete imgEl._pausedSrc;
                    this.attachHealthMonitor(stream.id, $item, itemStreamType);
                }
            }
            else if (stream.type === 'SNAPSHOT') {
                const imgEl = $item.find('.stream-snapshot-img')[0] || $video[0];
                if (imgEl) {
                    console.log(`[Fullscreen] Resuming snapshot polling: ${stream.id}`);
                    try {
                        await this.snapshotManager.startStream(stream.id, imgEl, stream.cameraType, 1000);
                        this.attachHealthMonitor(stream.id, $item, itemStreamType);
                    } catch (e) {
                        console.error(`[Fullscreen] Failed to resume snapshot stream ${stream.id}:`, e);
                    }
                }
            }
            else if (stream.type === 'WEBRTC') {
                if (videoEl) {
                    console.log(`[Fullscreen] Resuming WebRTC stream: ${stream.id}`);
                    const streamSubType = videoEl._webrtcStreamType || 'sub';
                    delete videoEl._webrtcStreamType;
                    try {
                        await this.webrtcManager.startStream(stream.id, videoEl, streamSubType);
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

    /**
     * Force exit fullscreen - emergency fallback when stream is frozen.
     *
     * Unlike closeFullscreen(), this method:
     * 1. Immediately removes CSS class (no async operations)
     * 2. Clears localStorage
     * 3. Resets processing flags
     * 4. Does NOT attempt to stop/restart streams (they may be frozen)
     *
     * User can then manually restart streams if needed.
     */
    forceExitFullscreen() {
        console.log('[Fullscreen] FORCE EXIT - bypassing stream operations');

        // Immediately remove fullscreen class from ALL stream items (safety)
        $('.stream-item.css-fullscreen').removeClass('css-fullscreen');

        // Clear localStorage
        localStorage.removeItem('fullscreenCameraSerial');

        // Restore grid layout (may have been set to grid-1 during fullscreen)
        this.setupLayout();

        // Reset all processing flags
        this._fullscreenProcessing = false;
        window._fullscreenLocked = false;

        console.log('[Fullscreen] FORCE EXIT complete - UI restored to grid view');

        // Resume paused streams and run fullscreen teardown after a short delay.
        // The delay lets the UI paint (css-fullscreen already removed above) before
        // async stream operations begin.  _resumePausedStreams() is called inside
        // closeFullscreen() via the early-return path (css-fullscreen already gone).
        setTimeout(() => {
            console.log('[Fullscreen] Running deferred stream resume...');
            this.closeFullscreen().catch(e => {
                console.warn('[Fullscreen] Deferred resume error (ignored):', e);
            });
        }, 500);
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
    /**
     * Persist the pinned camera serial to the server (user_preferences.pinned_camera).
     * Also updates localStorage (already done by caller before this is invoked).
     * @param {string|null} serial - Camera serial to pin, or null to clear
     */
    _savePinnedCamera(serial) {
        fetch('/api/my-preferences', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pinned_camera: serial })
        }).catch(err => console.warn('[Pin] Failed to save pinned camera preference:', err));
    }

    /**
     * Pause all background video streams when a pinned camera is expanded.
     * The backdrop goes fully black; background streams stop decoding.
     * @param {string} pinnedSerial - Serial of the pinned (expanded) camera to skip
     */
    _pauseBackgroundStreamsForPin(pinnedSerial) {
        this.$container.find('.stream-item').each((_, el) => {
            const serial = $(el).data('camera-serial');
            if (serial === pinnedSerial) return;
            const video = el.querySelector('video');
            if (video && !video.paused) {
                video.pause();
                $(el).data('pin-paused', true);
            }
        });
        console.log(`[Pin] Background streams paused (locked for ${pinnedSerial})`);
    }

    /**
     * Reload background streams that were paused by pin lock.
     * Calls play() which makes HLS seek to the live edge automatically.
     * If a stream was frozen too long, the HLS latency meter will handle recovery.
     * @param {string} pinnedSerial - Serial of the camera that was pinned
     */
    _reloadBackgroundStreamsAfterPin(pinnedSerial) {
        let resumed = 0;
        this.$container.find('.stream-item').each((_, el) => {
            if (!$(el).data('pin-paused')) return;
            const video = el.querySelector('video');
            if (video) {
                video.play().catch(() => {});
                resumed++;
            }
            $(el).removeData('pin-paused');
        });
        console.log(`[Pin] Background streams resumed (${resumed} restarted after ${pinnedSerial} unlock)`);
    }

    async expandCamera($streamItem) {
        // First exit fullscreen if active (mutual exclusivity with fullscreen mode)
        const $fullscreenItem = $('.stream-item.css-fullscreen');
        if ($fullscreenItem.length > 0) {
            console.log('[Expanded] Exiting fullscreen before expanding');
            await this.closeFullscreen();
        }

        // Then collapse any already expanded camera
        this.collapseExpandedCamera();

        // Arrange mode is grid-view only — exit it before opening modal
        if (tileArrangeManager.arrangeMode) {
            tileArrangeManager.exitArrangeMode(false);
        }

        const cameraId = $streamItem.data('camera-serial');
        console.log(`[Expanded] Opening modal for ${cameraId}`);

        // Show backdrop
        $('#expanded-backdrop').addClass('visible');

        // Add expanded class to stream item
        $streamItem.addClass('expanded');

        // Sync HD button visual state from localStorage
        try {
            const hdList = JSON.parse(localStorage.getItem('hdCameras') || '[]');
            const $hdBtn = $streamItem.find('.stream-hd-btn');
            if (hdList.includes(cameraId)) {
                $hdBtn.addClass('hd-active');
                $hdBtn.find('.hd-btn-label').text('HD');
            } else {
                $hdBtn.removeClass('hd-active');
                $hdBtn.find('.hd-btn-label').text('SD');
            }
        } catch (e) { /* non-critical */ }

        // Sync pin button visual state from localStorage
        const pinnedSerial = localStorage.getItem('pinnedCamera');
        const $pinBtn = $streamItem.find('.stream-pin-btn');
        if (pinnedSerial === cameraId) {
            $pinBtn.addClass('pin-active');
            $pinBtn.attr('title', 'Pinned — click to unpin (backdrop click disabled while pinned)');
            // Pinned lock: fully black backdrop + pause all background streams
            $('#expanded-backdrop').addClass('pinned-lock');
            this._pauseBackgroundStreamsForPin(cameraId);
        } else {
            $pinBtn.removeClass('pin-active');
            $pinBtn.attr('title', 'Pin: keep this camera in expanded view across reloads');
            $('#expanded-backdrop').removeClass('pinned-lock');
        }

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

        // Close mobile more menu if open
        $expandedItem.find('.stream-more-menu').removeClass('menu-visible');

        // Hide backdrop + lift pinned lock
        const $backdrop = $('#expanded-backdrop');
        const wasPinnedLock = $backdrop.hasClass('pinned-lock');
        $backdrop.removeClass('visible pinned-lock');

        // Remove expanded class
        $expandedItem.removeClass('expanded');

        // Restore body scroll
        $('body').css('overflow', '');

        // If pinned lock was active, reload background streams
        if (wasPinnedLock) {
            this._reloadBackgroundStreamsAfterPin(cameraId);
        }
    }

    /**
     * Sync the hamburger menu item active states with the real button states.
     * Called when the menu is opened to reflect current toggle states.
     *
     * @param {jQuery} $streamItem - The stream item containing the menu
     */
    _syncMoreMenuStates($streamItem) {
        const $menu = $streamItem.find('.stream-more-menu');

        // Audio active state
        const audioActive = $streamItem.find('.stream-audio-btn').hasClass('audio-active');
        $menu.find('[data-action="audio"]').toggleClass('active', audioActive);

        // PTZ active state
        const ptzActive = $streamItem.find('.stream-ptz-toggle-btn').hasClass('ptz-active');
        $menu.find('[data-action="ptz"]').toggleClass('active', ptzActive);

        // Controls active state
        const controlsActive = $streamItem.find('.stream-controls-toggle-btn').hasClass('controls-active');
        $menu.find('[data-action="controls"]').toggleClass('active', controlsActive);

        // Recording state
        const isRecording = $streamItem.find('.camera-record-btn').attr('data-recording') === 'true';
        $menu.find('[data-action="record"]').toggleClass('active', isRecording);
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

    // =========================================================================
    // Camera Selector Event Handlers
    // =========================================================================
    // These handlers respond to events from the camera-selector-controller
    // when user shows/hides cameras or changes HD/SD quality settings.
    // =========================================================================

    /**
     * Setup event handlers for camera selector integration
     * Called from setupEventListeners()
     */
    setupCameraSelectorHandlers() {
        // Handle camera being shown (re-enable stream)
        this.$container.on('camera-selector:show', '.stream-item', async (e) => {
            const $streamItem = $(e.currentTarget);
            const cameraId = $streamItem.data('camera-serial');
            const cameraType = $streamItem.data('camera-type');
            const streamType = $streamItem.data('stream-type');

            // Check if this camera should be in HD mode
            const hdCameras = this._getHDCameras();
            const quality = hdCameras.includes(cameraId) ? 'main' : 'sub';

            if (quality === 'main') {
                $streamItem.addClass('hd-mode');
            }

            console.log(`[CameraSelector] Restarting stream for ${cameraId} (quality: ${quality})`);
            await this.startStream(cameraId, $streamItem, cameraType, streamType, quality);
        });

        // Handle quality change (HD/SD toggle)
        this.$container.on('camera-selector:quality-change', '.stream-item', async (e, data) => {
            const $streamItem = $(e.currentTarget);
            const cameraId = $streamItem.data('camera-serial');
            const streamType = $streamItem.data('stream-type');
            const videoEl = $streamItem.find('.stream-video')[0];
            const quality = data?.quality || 'sub';

            console.log(`[CameraSelector] Switching ${cameraId} to ${quality} quality`);

            // Update HD mode class
            if (quality === 'main') {
                $streamItem.addClass('hd-mode');
            } else {
                $streamItem.removeClass('hd-mode');
            }

            // Stop current stream
            this.hlsManager.stopStream(cameraId);
            this.webrtcManager.stopStream(cameraId);

            // Start with new quality
            if (videoEl && (streamType === 'HLS' || streamType === 'LL_HLS' || streamType === 'NEOLINK' || streamType === 'NEOLINK_LL_HLS')) {
                await this.hlsManager.startStream(cameraId, videoEl, quality);
            } else if (videoEl && streamType === 'WEBRTC') {
                await this.webrtcManager.startStream(cameraId, videoEl, quality);
            }

            this.setStreamStatus($streamItem, 'live', quality === 'main' ? 'HD' : 'Live');
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
