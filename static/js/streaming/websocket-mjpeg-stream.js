/**
 * WebSocket MJPEG Stream Manager
 *
 * Multiplexes all MJPEG camera streams over a single WebSocket connection,
 * bypassing the browser's ~6 concurrent HTTP connection limit per domain.
 *
 * Architecture:
 * - Single WebSocket connection to /mjpeg namespace
 * - Server sends base64-encoded JPEG frames with camera ID prefixes
 * - Client demultiplexes frames to appropriate <canvas> or <img> elements
 *
 * Performance benefits:
 * - HTTP MJPEG: 16 cameras = 16 connections, but browser allows only ~6 at once
 *   → 10 cameras wait in queue, causing slow/staggered loading
 * - WebSocket MJPEG: All 16 cameras over 1 TCP connection
 *   → Instant frame delivery for all cameras simultaneously
 */

export class WebSocketMJPEGStreamManager {
    constructor() {
        // Socket.IO connection
        this.socket = null;
        this.connected = false;
        this.subscribedCameras = new Set();

        // Camera element mappings: cameraId -> {element, canvas, ctx, type}
        this.cameraElements = new Map();

        // Stats for debugging
        this.stats = {
            framesReceived: 0,
            lastFrameTime: 0,
            connectionTime: 0
        };

        // Reconnection settings
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 10;
        this.reconnectDelay = 1000; // Start with 1 second

        // Event callbacks
        this.onConnected = null;
        this.onDisconnected = null;
        this.onError = null;
    }

    /**
     * Connect to WebSocket MJPEG server.
     *
     * Loads Socket.IO client library if not already loaded, then establishes
     * connection to the /mjpeg namespace.
     *
     * @returns {Promise<boolean>} True if connection successful
     */
    async connect() {
        // Load Socket.IO client if needed
        if (typeof io === 'undefined') {
            await this._loadSocketIOClient();
        }

        return new Promise((resolve, reject) => {
            try {
                // Connect to /mjpeg namespace
                const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                const host = window.location.host;

                console.log('[WS-MJPEG] Connecting to WebSocket...');

                this.socket = io('/mjpeg', {
                    transports: ['websocket', 'polling'],
                    reconnection: true,
                    reconnectionAttempts: this.maxReconnectAttempts,
                    reconnectionDelay: this.reconnectDelay,
                    timeout: 10000
                });

                // Connection event handlers
                this.socket.on('connect', () => {
                    console.log('[WS-MJPEG] Connected to server');
                    this.connected = true;
                    this.stats.connectionTime = Date.now();
                    this.reconnectAttempts = 0;
                    this.reconnectDelay = 1000;

                    if (this.onConnected) {
                        this.onConnected();
                    }

                    resolve(true);
                });

                this.socket.on('connected', (data) => {
                    console.log('[WS-MJPEG] Server confirmed connection:', data);
                });

                this.socket.on('disconnect', (reason) => {
                    console.log(`[WS-MJPEG] Disconnected: ${reason}`);
                    this.connected = false;

                    if (this.onDisconnected) {
                        this.onDisconnected(reason);
                    }
                });

                this.socket.on('connect_error', (error) => {
                    console.error('[WS-MJPEG] Connection error:', error);
                    this.reconnectAttempts++;

                    if (this.onError) {
                        this.onError(error);
                    }

                    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
                        reject(new Error('Max reconnection attempts reached'));
                    }
                });

                // MJPEG frame handler - the main event
                this.socket.on('mjpeg_frames', (data) => {
                    this._handleFrames(data);
                });

                // Subscription confirmation
                this.socket.on('subscribed', (data) => {
                    console.log(`[WS-MJPEG] Subscribed to ${data.count} cameras:`, data.cameras);
                });

                this.socket.on('unsubscribed', () => {
                    console.log('[WS-MJPEG] Unsubscribed from all cameras');
                    this.subscribedCameras.clear();
                });

                this.socket.on('error', (data) => {
                    console.error('[WS-MJPEG] Server error:', data.message);
                    if (this.onError) {
                        this.onError(new Error(data.message));
                    }
                });

            } catch (error) {
                console.error('[WS-MJPEG] Failed to connect:', error);
                reject(error);
            }
        });
    }

    /**
     * Load Socket.IO client library dynamically.
     *
     * @returns {Promise<void>}
     */
    _loadSocketIOClient() {
        return new Promise((resolve, reject) => {
            // Check if already loaded
            if (typeof io !== 'undefined') {
                resolve();
                return;
            }

            const script = document.createElement('script');
            script.src = 'https://cdn.socket.io/4.7.4/socket.io.min.js';
            script.crossOrigin = 'anonymous';

            script.onload = () => {
                console.log('[WS-MJPEG] Socket.IO client loaded');
                resolve();
            };

            script.onerror = () => {
                reject(new Error('Failed to load Socket.IO client'));
            };

            document.head.appendChild(script);
        });
    }

    /**
     * Subscribe to camera streams.
     *
     * @param {string[]} cameraIds - Array of camera serial numbers
     * @param {Map<string, HTMLElement>} elementMap - Map of cameraId -> element to display frames
     */
    subscribe(cameraIds, elementMap) {
        if (!this.connected) {
            console.error('[WS-MJPEG] Not connected - cannot subscribe');
            return false;
        }

        // Register elements for each camera
        for (const [cameraId, element] of elementMap) {
            this._registerElement(cameraId, element);
            this.subscribedCameras.add(cameraId);
        }

        // Send subscription request to server
        this.socket.emit('subscribe', {
            cameras: cameraIds
        });

        console.log(`[WS-MJPEG] Subscribing to ${cameraIds.length} cameras`);
        return true;
    }

    /**
     * Register an element to receive frames for a camera.
     *
     * Supports both <img> and <canvas> elements. For <img>, frames are set as
     * data URLs. For <canvas>, frames are drawn directly for better performance.
     *
     * @param {string} cameraId - Camera serial number
     * @param {HTMLElement} element - <img> or <canvas> element
     */
    _registerElement(cameraId, element) {
        if (!element) {
            console.warn(`[WS-MJPEG] No element provided for camera ${cameraId}`);
            return;
        }

        const info = {
            element: element,
            type: element.tagName.toLowerCase(),
            canvas: null,
            ctx: null
        };

        // If it's a canvas, get 2D context for direct drawing
        if (info.type === 'canvas') {
            info.canvas = element;
            info.ctx = element.getContext('2d');
        }
        // If it's an img element, we'll use data URLs
        else if (info.type === 'img') {
            // Will use element.src = 'data:image/jpeg;base64,...'
        }
        // For video elements, we'll need to create a canvas overlay
        else if (info.type === 'video') {
            // Create a canvas to overlay the video element
            const canvas = document.createElement('canvas');
            canvas.className = 'ws-mjpeg-canvas';
            canvas.style.cssText = 'position: absolute; top: 0; left: 0; width: 100%; height: 100%; object-fit: cover;';

            // Insert canvas after video
            element.parentNode.insertBefore(canvas, element.nextSibling);
            element.style.display = 'none'; // Hide video element

            info.canvas = canvas;
            info.ctx = canvas.getContext('2d');
        }

        this.cameraElements.set(cameraId, info);
        console.log(`[WS-MJPEG] Registered ${info.type} element for camera ${cameraId}`);
    }

    /**
     * Handle incoming frame batch from server.
     *
     * Server sends: {frames: [{camera_id, frame, frame_num, is_error}, ...], timestamp}
     *
     * @param {Object} data - Frame batch data
     */
    _handleFrames(data) {
        const frames = data.frames || [];
        const timestamp = data.timestamp;

        for (const frameData of frames) {
            const { camera_id, frame, frame_num, is_error } = frameData;

            const elementInfo = this.cameraElements.get(camera_id);
            if (!elementInfo) {
                // Camera not registered - skip silently (might have unsubscribed)
                continue;
            }

            this.stats.framesReceived++;
            this.stats.lastFrameTime = Date.now();

            // Decode and display frame
            this._displayFrame(elementInfo, frame, is_error);
        }
    }

    /**
     * Display a frame on the registered element.
     *
     * @param {Object} elementInfo - Element info from cameraElements map
     * @param {string} base64Frame - Base64-encoded JPEG data
     * @param {boolean} isError - Whether this is an error placeholder frame
     */
    _displayFrame(elementInfo, base64Frame, isError) {
        const dataUrl = `data:image/jpeg;base64,${base64Frame}`;

        if (elementInfo.type === 'img') {
            // Direct assignment to img.src
            elementInfo.element.src = dataUrl;
        }
        else if (elementInfo.canvas && elementInfo.ctx) {
            // Draw on canvas for better performance
            const img = new Image();
            img.onload = () => {
                const canvas = elementInfo.canvas;
                const ctx = elementInfo.ctx;

                // Resize canvas if needed
                if (canvas.width !== img.width || canvas.height !== img.height) {
                    canvas.width = img.width;
                    canvas.height = img.height;
                }

                ctx.drawImage(img, 0, 0);
            };
            img.src = dataUrl;
        }
    }

    /**
     * Unsubscribe from all camera streams.
     */
    unsubscribe() {
        if (this.socket && this.connected) {
            this.socket.emit('unsubscribe');
        }

        this.subscribedCameras.clear();

        // Clean up canvas overlays
        for (const [cameraId, info] of this.cameraElements) {
            if (info.type === 'video' && info.canvas) {
                info.canvas.remove();
                info.element.style.display = '';
            }
        }
        this.cameraElements.clear();
    }

    /**
     * Disconnect from WebSocket server.
     */
    disconnect() {
        this.unsubscribe();

        if (this.socket) {
            this.socket.disconnect();
            this.socket = null;
        }

        this.connected = false;
        console.log('[WS-MJPEG] Disconnected');
    }

    /**
     * Check if connected to server.
     *
     * @returns {boolean}
     */
    isConnected() {
        return this.connected && this.socket?.connected;
    }

    /**
     * Get connection statistics.
     *
     * @returns {Object} Stats object
     */
    getStats() {
        return {
            ...this.stats,
            connected: this.connected,
            subscribedCameras: Array.from(this.subscribedCameras),
            uptime: this.connected ? Date.now() - this.stats.connectionTime : 0
        };
    }
}

// Export singleton instance for easy use
export const webSocketMJPEGManager = new WebSocketMJPEGStreamManager();
