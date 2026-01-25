/**
 * Talkback Manager
 * Location: ~/0_NVR/static/js/streaming/talkback-manager.js
 *
 * Handles two-way audio (talkback) functionality for cameras.
 * Uses getUserMedia for microphone capture and WebSocket for audio transmission.
 *
 * Architecture:
 *   Browser Microphone -> ScriptProcessor -> WebSocket -> Flask -> Eufy Bridge -> Camera
 *
 * Audio Format:
 *   - Sample rate: 16kHz (telephony quality, good for voice)
 *   - Channels: Mono
 *   - Bit depth: 16-bit signed PCM
 *   - Transmission: Base64 encoded over WebSocket
 *
 * Author: NVR System
 * Date: January 25, 2026
 */

/**
 * TalkbackManager class
 *
 * Singleton pattern - use TalkbackManager.getInstance() or the exported instance.
 */
export class TalkbackManager {
    constructor() {
        // WebSocket connection to /talkback namespace
        this.socket = null;

        // Web Audio API context for processing microphone input
        this.audioContext = null;

        // MediaStream from getUserMedia
        this.mediaStream = null;

        // ScriptProcessorNode for audio processing
        // NOTE: ScriptProcessorNode is deprecated but AudioWorklet has broader
        // compatibility issues. We use ScriptProcessor for now.
        this.scriptProcessor = null;

        // Currently active camera (only one talkback session at a time)
        this.activeCameraId = null;

        // Connection state
        this.isConnected = false;
        this.isTalking = false;

        // Microphone permission state
        this.microphonePermission = 'unknown'; // 'granted', 'denied', 'prompt', 'unknown'

        // Event callbacks
        this._onStateChange = null;
        this._onError = null;

        // Bind methods for event handlers
        this._handleAudioProcess = this._handleAudioProcess.bind(this);
    }

    /**
     * Request microphone permission from the user.
     *
     * This should be called early (e.g., on first talkback button interaction)
     * to trigger the browser permission prompt before the user holds the PTT button.
     *
     * @returns {Promise<boolean>} True if permission granted
     */
    async requestMicrophonePermission() {
        console.log('[TalkbackManager] Requesting microphone permission');

        try {
            // Request microphone with optimal settings for voice
            this.mediaStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    echoCancellation: true,      // Prevent feedback loops
                    noiseSuppression: true,       // Reduce background noise
                    autoGainControl: true,        // Normalize volume levels
                    sampleRate: 16000,            // 16kHz for voice
                    channelCount: 1               // Mono
                }
            });

            this.microphonePermission = 'granted';
            console.log('[TalkbackManager] Microphone permission granted');
            return true;

        } catch (error) {
            console.error('[TalkbackManager] Microphone permission error:', error);

            if (error.name === 'NotAllowedError' || error.name === 'PermissionDeniedError') {
                this.microphonePermission = 'denied';
            } else if (error.name === 'NotFoundError') {
                this.microphonePermission = 'not_found';
            } else {
                this.microphonePermission = 'error';
            }

            this._notifyError(`Microphone access denied: ${error.message}`);
            return false;
        }
    }

    /**
     * Check current microphone permission status.
     *
     * Uses the Permissions API if available, otherwise returns cached state.
     *
     * @returns {Promise<string>} Permission state: 'granted', 'denied', 'prompt', or 'unknown'
     */
    async checkMicrophonePermission() {
        if (navigator.permissions && navigator.permissions.query) {
            try {
                const result = await navigator.permissions.query({ name: 'microphone' });
                this.microphonePermission = result.state;
                return result.state;
            } catch (e) {
                // Some browsers don't support querying microphone permission
                console.log('[TalkbackManager] Permissions API not available for microphone');
            }
        }
        return this.microphonePermission;
    }

    /**
     * Connect to the talkback WebSocket namespace.
     *
     * Creates a Socket.IO connection to /talkback for sending audio data.
     *
     * @returns {Promise<boolean>} True if connected successfully
     */
    async connect() {
        if (this.isConnected && this.socket) {
            console.log('[TalkbackManager] Already connected');
            return true;
        }

        console.log('[TalkbackManager] Connecting to /talkback namespace');

        return new Promise((resolve, reject) => {
            try {
                // Connect to /talkback namespace
                this.socket = io('/talkback', {
                    reconnection: true,
                    reconnectionAttempts: 3,
                    reconnectionDelay: 1000,
                    timeout: 10000
                });

                // Handle connection success
                this.socket.on('connected', (data) => {
                    console.log('[TalkbackManager] Connected:', data);
                    this.isConnected = true;
                    this._notifyStateChange('connected');
                    resolve(true);
                });

                // Handle connection error
                this.socket.on('connect_error', (error) => {
                    console.error('[TalkbackManager] Connection error:', error);
                    this.isConnected = false;
                    this._notifyError(`Connection failed: ${error.message}`);
                    reject(error);
                });

                // Handle disconnection
                this.socket.on('disconnect', (reason) => {
                    console.log('[TalkbackManager] Disconnected:', reason);
                    this.isConnected = false;
                    this.isTalking = false;
                    this.activeCameraId = null;
                    this._notifyStateChange('disconnected');
                });

                // Handle talkback events from server
                this.socket.on('talkback_started', (data) => {
                    console.log('[TalkbackManager] Talkback started:', data);
                    this.isTalking = true;
                    this._notifyStateChange('talking');
                });

                this.socket.on('talkback_stopped', (data) => {
                    console.log('[TalkbackManager] Talkback stopped:', data);
                    this.isTalking = false;
                    this.activeCameraId = null;
                    this._notifyStateChange('idle');
                });

                this.socket.on('talkback_error', (data) => {
                    console.error('[TalkbackManager] Server error:', data);
                    this.isTalking = false;
                    this._notifyError(data.error || 'Talkback error');
                });

            } catch (error) {
                console.error('[TalkbackManager] Failed to create socket:', error);
                reject(error);
            }
        });
    }

    /**
     * Start talkback session with a camera.
     *
     * This is called when the user presses/holds the PTT button.
     * It connects to the server, starts the talkback session, and begins
     * capturing audio from the microphone.
     *
     * @param {string} cameraId - Camera serial number
     * @returns {Promise<boolean>} True if talkback started successfully
     */
    async startTalkback(cameraId) {
        console.log(`[TalkbackManager] Starting talkback for ${cameraId}`);

        // Don't start if already talking
        if (this.isTalking) {
            console.log('[TalkbackManager] Already in a talkback session');
            return false;
        }

        try {
            // Ensure we have microphone permission
            if (!this.mediaStream) {
                const hasPermission = await this.requestMicrophonePermission();
                if (!hasPermission) {
                    return false;
                }
            }

            // Ensure WebSocket is connected
            if (!this.isConnected) {
                await this.connect();
            }

            // Store active camera
            this.activeCameraId = cameraId;

            // Tell server to start talkback
            this.socket.emit('start_talkback', { camera_id: cameraId });

            // Set up audio processing
            await this._setupAudioProcessing();

            return true;

        } catch (error) {
            console.error('[TalkbackManager] Start talkback error:', error);
            this._notifyError(`Failed to start talkback: ${error.message}`);
            return false;
        }
    }

    /**
     * Stop talkback session.
     *
     * Called when the user releases the PTT button.
     * Stops audio capture and notifies the server.
     */
    stopTalkback() {
        console.log(`[TalkbackManager] Stopping talkback for ${this.activeCameraId}`);

        if (!this.activeCameraId) {
            console.log('[TalkbackManager] No active talkback session');
            return;
        }

        // Stop audio processing
        this._teardownAudioProcessing();

        // Tell server to stop talkback
        if (this.socket && this.isConnected) {
            this.socket.emit('stop_talkback', { camera_id: this.activeCameraId });
        }

        // Reset state
        this.isTalking = false;
        this.activeCameraId = null;
        this._notifyStateChange('idle');
    }

    /**
     * Set up Web Audio API for microphone processing.
     *
     * Creates AudioContext and ScriptProcessorNode to capture and process
     * audio frames for transmission.
     */
    async _setupAudioProcessing() {
        if (!this.mediaStream) {
            throw new Error('No media stream available');
        }

        console.log('[TalkbackManager] Setting up audio processing');

        // Create AudioContext with 16kHz sample rate
        // Note: Some browsers may resample internally
        this.audioContext = new (window.AudioContext || window.webkitAudioContext)({
            sampleRate: 16000
        });

        // Create source from microphone stream
        const source = this.audioContext.createMediaStreamSource(this.mediaStream);

        // Create ScriptProcessor for capturing audio frames
        // Buffer size: 4096 samples (~256ms at 16kHz)
        // Input channels: 1 (mono)
        // Output channels: 1 (mono)
        this.scriptProcessor = this.audioContext.createScriptProcessor(4096, 1, 1);

        // Process audio frames
        this.scriptProcessor.onaudioprocess = this._handleAudioProcess;

        // Connect: microphone -> processor -> destination (required for processor to work)
        source.connect(this.scriptProcessor);
        this.scriptProcessor.connect(this.audioContext.destination);

        console.log('[TalkbackManager] Audio processing started');
    }

    /**
     * Handle audio processing event.
     *
     * Called by ScriptProcessorNode for each audio buffer.
     * Converts float32 audio to int16 PCM and sends via WebSocket.
     *
     * @param {AudioProcessingEvent} e - Audio processing event
     */
    _handleAudioProcess(e) {
        if (!this.isTalking || !this.activeCameraId || !this.socket) {
            return;
        }

        // Get float32 audio data from input buffer
        const float32Data = e.inputBuffer.getChannelData(0);

        // Convert to 16-bit signed PCM
        const int16Data = this._floatTo16BitPCM(float32Data);

        // Convert to base64 for transmission
        const base64Audio = this._arrayBufferToBase64(int16Data.buffer);

        // Send to server
        this.socket.emit('audio_frame', {
            camera_id: this.activeCameraId,
            audio_data: base64Audio
        });
    }

    /**
     * Tear down audio processing.
     *
     * Disconnects and closes AudioContext and processors.
     */
    _teardownAudioProcessing() {
        console.log('[TalkbackManager] Tearing down audio processing');

        if (this.scriptProcessor) {
            this.scriptProcessor.disconnect();
            this.scriptProcessor.onaudioprocess = null;
            this.scriptProcessor = null;
        }

        if (this.audioContext && this.audioContext.state !== 'closed') {
            this.audioContext.close().catch(e => {
                console.warn('[TalkbackManager] Error closing AudioContext:', e);
            });
            this.audioContext = null;
        }
    }

    /**
     * Convert Float32Array audio samples to Int16Array (16-bit PCM).
     *
     * Float32 audio values are in range [-1.0, 1.0].
     * Int16 values are in range [-32768, 32767].
     *
     * @param {Float32Array} float32Array - Input audio samples
     * @returns {Int16Array} - Output 16-bit PCM samples
     */
    _floatTo16BitPCM(float32Array) {
        const int16 = new Int16Array(float32Array.length);
        for (let i = 0; i < float32Array.length; i++) {
            // Clamp value to [-1, 1] range and scale to 16-bit
            const s = Math.max(-1, Math.min(1, float32Array[i]));
            int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        return int16;
    }

    /**
     * Convert ArrayBuffer to base64 string.
     *
     * @param {ArrayBuffer} buffer - Binary data
     * @returns {string} - Base64 encoded string
     */
    _arrayBufferToBase64(buffer) {
        const bytes = new Uint8Array(buffer);
        let binary = '';
        for (let i = 0; i < bytes.byteLength; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        return btoa(binary);
    }

    /**
     * Set callback for state changes.
     *
     * @param {Function} callback - Called with state: 'connected', 'disconnected', 'talking', 'idle'
     */
    onStateChange(callback) {
        this._onStateChange = callback;
    }

    /**
     * Set callback for errors.
     *
     * @param {Function} callback - Called with error message string
     */
    onError(callback) {
        this._onError = callback;
    }

    /**
     * Notify state change callback.
     * @private
     */
    _notifyStateChange(state) {
        if (this._onStateChange) {
            this._onStateChange(state);
        }
    }

    /**
     * Notify error callback.
     * @private
     */
    _notifyError(error) {
        if (this._onError) {
            this._onError(error);
        }
    }

    /**
     * Clean up all resources.
     *
     * Should be called when the manager is no longer needed.
     */
    destroy() {
        console.log('[TalkbackManager] Destroying');

        this.stopTalkback();

        if (this.socket) {
            this.socket.disconnect();
            this.socket = null;
        }

        if (this.mediaStream) {
            this.mediaStream.getTracks().forEach(track => track.stop());
            this.mediaStream = null;
        }

        this.isConnected = false;
    }
}

// Export singleton instance
export const talkbackManager = new TalkbackManager();
