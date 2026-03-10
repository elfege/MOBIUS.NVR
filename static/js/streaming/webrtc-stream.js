/**
 * WebRTC Stream Manager - ES6 Module
 * Handles WebRTC streaming via MediaMTX WHEP protocol for sub-second latency
 *
 * WHEP (WebRTC-HTTP Egress Protocol) flow:
 * 1. Browser creates RTCPeerConnection with recvonly video transceiver
 * 2. Browser sends SDP offer to MediaMTX /camera_id/whep endpoint
 * 3. MediaMTX responds with SDP answer
 * 4. ICE candidates are exchanged (Trickle ICE or bundled)
 * 5. Browser receives RTP video directly from MediaMTX
 *
 * Latency: ~200-500ms (vs 2-4s for LL-HLS)
 */

export class WebRTCStreamManager {
    constructor() {
        // Map: cameraId -> { pc: RTCPeerConnection, element: HTMLVideoElement, startTime: number }
        this.activeStreams = new Map();
        this.retryAttempts = new Map();

        // MediaMTX WebRTC port (configured in mediamtx.yml)
        this.webrtcPort = 8889;
    }

    /**
     * Create latency overlay badge (same pattern as HLS manager)
     */
    _ensureLatencyOverlay(videoEl) {
        if (videoEl._latencyOverlay) return videoEl._latencyOverlay;

        const badge = document.createElement('div');
        badge.className = 'latency-badge webrtc-badge';
        Object.assign(badge.style, {
            padding: '2px 6px',
            fontSize: '12px',
            lineHeight: '16px',
            background: 'rgba(0,128,0,0.7)',  // Green for WebRTC (low latency)
            color: '#fff',
            borderRadius: '6px',
            fontFamily: 'system-ui, -apple-system, Segoe UI, Roboto, sans-serif',
            pointerEvents: 'none',
            whiteSpace: 'nowrap',
        });

        // Insert into .stream-bottom-bar flex container (alongside HD + pin buttons).
        const streamItem = videoEl.closest('.stream-item');
        const bar = streamItem && streamItem.querySelector('.stream-bottom-bar');
        if (bar) {
            bar.prepend(badge);
        } else {
            const parent = videoEl.parentElement || document.body;
            badge.style.position = 'absolute';
            badge.style.left = '8px';
            badge.style.bottom = '8px';
            badge.style.zIndex = '2';
            parent.style.position = parent.style.position || 'relative';
            parent.appendChild(badge);
        }
        videoEl._latencyOverlay = badge;
        return badge;
    }

    /**
     * Attach latency meter for WebRTC
     * WebRTC doesn't have programDateTime like HLS, so we estimate based on
     * video.currentTime progression and connection stats
     */
    _attachLatencyMeter(pc, videoEl) {
        const overlay = this._ensureLatencyOverlay(videoEl);

        // Update latency display from WebRTC stats
        if (videoEl._latencyTimer) clearInterval(videoEl._latencyTimer);
        videoEl._latencyTimer = setInterval(async () => {
            try {
                const stats = await pc.getStats();
                let jitter = 0;
                let packetsLost = 0;
                let packetsReceived = 0;

                stats.forEach(report => {
                    if (report.type === 'inbound-rtp' && report.kind === 'video') {
                        jitter = report.jitter || 0;
                        packetsLost = report.packetsLost || 0;
                        packetsReceived = report.packetsReceived || 0;
                    }
                });

                // Display estimated latency (jitter * 1000 for ms, plus base ~200ms)
                const estimatedLatency = Math.round(200 + (jitter * 1000));
                const lossRate = packetsReceived > 0
                    ? ((packetsLost / (packetsLost + packetsReceived)) * 100).toFixed(1)
                    : 0;

                overlay.textContent = `~${estimatedLatency}ms`;
                overlay.title = `WebRTC | Loss: ${lossRate}%`;
                overlay.style.display = '';
            } catch (e) {
                // Stats not available yet
                overlay.textContent = 'WebRTC';
            }
        }, 1000);  // Update every second (stats polling is heavier than HLS)

        videoEl._latencyDetach = () => {
            if (videoEl._latencyTimer) {
                clearInterval(videoEl._latencyTimer);
                videoEl._latencyTimer = null;
            }
            if (videoEl._latencyOverlay) {
                videoEl._latencyOverlay.textContent = '';
            }
        };
    }

    /**
     * Start WebRTC stream for a camera via WHEP protocol
     *
     * @param {string} cameraId - Camera identifier (used as MediaMTX path)
     * @param {HTMLVideoElement} videoElement - Video element to attach stream
     * @param {string} streamType - 'sub' or 'main' (sub is default for grid view)
     * @returns {Promise<boolean>} - Resolves true on success
     */
    async startStream(cameraId, videoElement, streamType = 'sub') {
        console.log(`[WebRTC] Starting stream for ${cameraId} (${streamType})`);

        try {
            // IMPORTANT: Notify backend to ensure FFmpeg is running and publishing
            // For dual-output FFmpeg, both sub and main streams come from ONE process
            // The backend needs to know we want main so it can return the correct MediaMTX path
            // and verify the FFmpeg is actually publishing to the _main path
            try {
                const apiResponse = await fetch(`/api/stream/start/${cameraId}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ type: streamType })
                });
                if (apiResponse.ok) {
                    const startInfo = await apiResponse.json();
                    console.log(`[WebRTC] Backend confirmed stream for ${cameraId}:`, startInfo);
                } else {
                    console.warn(`[WebRTC] Backend API returned ${apiResponse.status} for ${cameraId}, continuing anyway`);
                }
            } catch (apiError) {
                console.warn(`[WebRTC] Backend API call failed for ${cameraId}:`, apiError);
                // Continue anyway - MediaMTX might still have the stream available
            }

            // Build WHEP URL - route through nginx proxy to avoid mixed content issues
            // When page is served over HTTPS, we can't fetch from HTTP port directly
            // Nginx proxies /webrtc/<path> -> nvr-packager:8889/<path>
            // Path format: camera_id for sub stream, camera_id_main for main stream
            const streamPath = streamType === 'main' ? `${cameraId}_main` : cameraId;
            const whepUrl = `${window.location.origin}/webrtc/${streamPath}/whep`;

            // Create RTCPeerConnection with minimal config (LAN-only, no STUN needed)
            const pc = new RTCPeerConnection({
                iceServers: []  // No STUN/TURN for LAN-only
            });

            // Add receive-only video transceiver
            pc.addTransceiver('video', { direction: 'recvonly' });
            pc.addTransceiver('audio', { direction: 'recvonly' });

            // Handle incoming track (video/audio)
            pc.ontrack = (event) => {
                console.log(`[WebRTC] ${cameraId}: Received track: ${event.track.kind}`);
                if (event.track.kind === 'video') {
                    videoElement.srcObject = event.streams[0];
                    videoElement.play().catch(err => {
                        console.warn(`[WebRTC] ${cameraId}: Autoplay prevented:`, err);
                    });
                }
            };

            // Monitor ICE connection state
            pc.oniceconnectionstatechange = () => {
                console.log(`[WebRTC] ${cameraId}: ICE state: ${pc.iceConnectionState}`);

                if (pc.iceConnectionState === 'connected' || pc.iceConnectionState === 'completed') {
                    // Dispatch event when stream is live
                    if (!videoElement._firstFrameReceived) {
                        videoElement._firstFrameReceived = true;
                        console.log(`[WebRTC] ${cameraId}: Stream is live`);
                        videoElement.dispatchEvent(new CustomEvent('streamlive', { detail: { cameraId } }));
                    }
                } else if (pc.iceConnectionState === 'disconnected' || pc.iceConnectionState === 'failed') {
                    console.error(`[WebRTC] ${cameraId}: Connection ${pc.iceConnectionState}`);
                    videoElement.dispatchEvent(new CustomEvent('streamerror', {
                        detail: { cameraId, error: `ICE ${pc.iceConnectionState}` }
                    }));
                }
            };

            // Create SDP offer
            const offer = await pc.createOffer();
            await pc.setLocalDescription(offer);

            // Wait for ICE gathering to complete (or timeout)
            await this._waitForIceGathering(pc, 2000);

            // Send offer to MediaMTX WHEP endpoint
            console.log(`[WebRTC] ${cameraId}: Sending offer to ${whepUrl}`);
            const response = await fetch(whepUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/sdp'
                },
                body: pc.localDescription.sdp
            });

            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`WHEP request failed: ${response.status} - ${errorText}`);
            }

            // Get SDP answer from MediaMTX
            const answerSdp = await response.text();
            await pc.setRemoteDescription({
                type: 'answer',
                sdp: answerSdp
            });

            // Store stream info
            this.activeStreams.set(cameraId, {
                pc: pc,
                element: videoElement,
                type: streamType,
                startTime: Date.now(),
                whepUrl: whepUrl
            });

            // Attach latency meter
            this._attachLatencyMeter(pc, videoElement);

            // Reset retry counter on success
            this.retryAttempts.delete(cameraId);

            console.log(`[WebRTC] ${cameraId}: Stream setup complete, waiting for media`);
            return true;

        } catch (error) {
            console.error(`[WebRTC] Failed to start stream for ${cameraId}:`, error);

            // Clean up on failure
            const stream = this.activeStreams.get(cameraId);
            if (stream?.pc) {
                stream.pc.close();
            }
            this.activeStreams.delete(cameraId);

            throw error;
        }
    }

    /**
     * Wait for ICE gathering to complete or timeout
     */
    _waitForIceGathering(pc, timeoutMs) {
        return new Promise((resolve) => {
            if (pc.iceGatheringState === 'complete') {
                resolve();
                return;
            }

            const timeout = setTimeout(() => {
                console.log('[WebRTC] ICE gathering timeout, proceeding with current candidates');
                resolve();
            }, timeoutMs);

            pc.onicegatheringstatechange = () => {
                if (pc.iceGatheringState === 'complete') {
                    clearTimeout(timeout);
                    resolve();
                }
            };
        });
    }

    /**
     * Stop WebRTC stream for a camera
     */
    stopStream(cameraId) {
        try {
            const stream = this.activeStreams.get(cameraId);
            if (stream) {
                // Close RTCPeerConnection
                if (stream.pc) {
                    stream.pc.close();
                }

                // Clear video element
                if (stream.element) {
                    stream.element.srcObject = null;
                    stream.element.pause();

                    // Clean up latency meter
                    if (stream.element._latencyDetach) {
                        stream.element._latencyDetach();
                        delete stream.element._latencyDetach;
                    }

                    // Reset first frame flag
                    delete stream.element._firstFrameReceived;
                }

                this.activeStreams.delete(cameraId);
                console.log(`[WebRTC] Stopped stream for ${cameraId}`);
                return true;
            }
            return false;
        } catch (error) {
            console.error(`[WebRTC] Failed to stop stream for ${cameraId}:`, error);

            // Force cleanup
            this.activeStreams.delete(cameraId);
            return false;
        }
    }

    /**
     * Check if stream is currently active
     */
    isStreamActive(cameraId) {
        const stream = this.activeStreams.get(cameraId);
        if (!stream) return false;

        // Also check if RTCPeerConnection is still connected
        const state = stream.pc?.iceConnectionState;
        return state === 'connected' || state === 'completed' || state === 'checking' || state === 'new';
    }

    /**
     * Stop all active WebRTC streams
     */
    stopAllStreams() {
        try {
            this.activeStreams.forEach((stream, cameraId) => {
                if (stream.pc) {
                    stream.pc.close();
                }
                if (stream.element) {
                    stream.element.srcObject = null;
                    stream.element.pause();

                    if (stream.element._latencyDetach) {
                        stream.element._latencyDetach();
                        delete stream.element._latencyDetach;
                    }
                }
            });
            this.activeStreams.clear();
            console.log('[WebRTC] Stopped all streams');
            return true;
        } catch (error) {
            console.error('[WebRTC] Failed to stop all streams:', error);
            this.activeStreams.clear();
            return false;
        }
    }

    /**
     * Get stream information for a camera
     */
    getStreamInfo(cameraId) {
        return this.activeStreams.get(cameraId) || null;
    }

    /**
     * Get all active stream IDs
     */
    getActiveStreamIds() {
        return Array.from(this.activeStreams.keys());
    }

    /**
     * Force refresh a stream by closing and reopening connection.
     *
     * Preserves the last frame during refresh to avoid visible black flash:
     * 1. Capture current video frame to canvas overlay
     * 2. Stop existing WebRTC connection
     * 3. Restart stream
     * 4. Remove canvas when new stream is live
     */
    async forceRefreshStream(cameraId, videoElement) {
        const current = this.activeStreams.get(cameraId);
        const streamType = current?.type ?? 'sub';

        // Capture last frame to canvas overlay for seamless transition
        let frameCanvas = null;
        if (videoElement && videoElement.readyState >= 2 && videoElement.videoWidth > 0) {
            frameCanvas = this._captureFrameOverlay(videoElement);
        }

        // Stop existing stream
        this.stopStream(cameraId);

        // Brief delay before restart
        await new Promise(resolve => setTimeout(resolve, 200));

        // Restart stream - pass canvas reference to remove when live
        const result = await this.startStream(cameraId, videoElement, streamType);

        // Remove frame overlay after short delay (stream should be displaying by now)
        // The 'streamlive' event is more reliable but this is a fallback
        if (frameCanvas) {
            setTimeout(() => {
                if (frameCanvas.parentNode) {
                    frameCanvas.remove();
                }
            }, 2000);
        }

        return result;
    }

    /**
     * Capture current video frame to a canvas overlay positioned over the video.
     *
     * Creates a canvas element that shows the last frame, positioned exactly
     * over the video element. This prevents the black flash during stream refresh.
     *
     * @param {HTMLVideoElement} videoElement - Video element to capture from
     * @returns {HTMLCanvasElement} Canvas element with captured frame
     */
    _captureFrameOverlay(videoElement) {
        const canvas = document.createElement('canvas');
        canvas.width = videoElement.videoWidth;
        canvas.height = videoElement.videoHeight;
        canvas.className = 'webrtc-frame-overlay';

        // Draw current frame
        const ctx = canvas.getContext('2d');
        ctx.drawImage(videoElement, 0, 0);

        // Position canvas exactly over video element
        canvas.style.cssText = `
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            object-fit: cover;
            z-index: 10;
            pointer-events: none;
        `;

        // Insert canvas as sibling after video (video parent should have position:relative)
        const parent = videoElement.parentElement;
        if (parent) {
            // Ensure parent has relative positioning for absolute canvas
            const parentStyle = window.getComputedStyle(parent);
            if (parentStyle.position === 'static') {
                parent.style.position = 'relative';
            }
            parent.appendChild(canvas);
            console.log(`[WebRTC] ${videoElement.id || 'video'}: Frame overlay created for seamless refresh`);
        }

        return canvas;
    }

    /**
     * Start WebRTC stream via go2rtc API (bypasses MediaMTX).
     *
     * go2rtc reads from Neolink RTSP and serves WebRTC directly, eliminating
     * FFmpeg transcoding and MediaMTX HLS segmenting from the path.
     * This fixes the ~12s latency caused by Neolink's broken DTS=0 timestamps
     * that FFmpeg can't handle well.
     *
     * go2rtc WebRTC API format:
     *   POST /api/webrtc?src=STREAM_NAME
     *   Body: SDP offer (application/sdp)
     *   Response: SDP answer (application/sdp)
     *
     * @param {string} cameraId - Camera serial (must match go2rtc.yaml stream name)
     * @param {HTMLVideoElement} videoElement - Video element to attach stream
     * @returns {Promise<boolean>} - Resolves true on success
     */
    async startGo2rtcStream(cameraId, videoElement) {
        console.log(`[go2rtc] Starting WebRTC stream for ${cameraId}`);

        try {
            // go2rtc API endpoint proxied through nginx
            const go2rtcUrl = `${window.location.origin}/go2rtc/api/webrtc?src=${cameraId}`;

            // Create RTCPeerConnection
            const pc = new RTCPeerConnection({
                iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
            });

            // Add receive-only transceivers
            pc.addTransceiver('video', { direction: 'recvonly' });
            pc.addTransceiver('audio', { direction: 'recvonly' });

            // Handle incoming tracks
            pc.ontrack = (event) => {
                console.log(`[go2rtc] ${cameraId}: Received track: ${event.track.kind}`);
                if (event.track.kind === 'video') {
                    videoElement.srcObject = event.streams[0];
                    videoElement.play().catch(err => {
                        console.warn(`[go2rtc] ${cameraId}: Autoplay prevented:`, err);
                    });
                }
            };

            // Monitor ICE connection state
            pc.oniceconnectionstatechange = () => {
                console.log(`[go2rtc] ${cameraId}: ICE state: ${pc.iceConnectionState}`);
                if (pc.iceConnectionState === 'connected' || pc.iceConnectionState === 'completed') {
                    if (!videoElement._firstFrameReceived) {
                        videoElement._firstFrameReceived = true;
                        console.log(`[go2rtc] ${cameraId}: Stream is live`);
                        videoElement.dispatchEvent(new CustomEvent('streamlive', { detail: { cameraId } }));
                    }
                } else if (pc.iceConnectionState === 'disconnected' || pc.iceConnectionState === 'failed') {
                    console.error(`[go2rtc] ${cameraId}: Connection ${pc.iceConnectionState}`);
                    videoElement.dispatchEvent(new CustomEvent('streamerror', {
                        detail: { cameraId, error: `ICE ${pc.iceConnectionState}` }
                    }));
                }
            };

            // Create SDP offer
            const offer = await pc.createOffer();
            await pc.setLocalDescription(offer);

            // Wait for ICE gathering
            await this._waitForIceGathering(pc, 2000);

            // Send offer to go2rtc API using JSON format
            // go2rtc expects: {"type":"offer","sdp":"v=0\r\n..."}
            // and returns:    {"type":"answer","sdp":"v=0\r\n..."}
            console.log(`[go2rtc] ${cameraId}: Sending offer to ${go2rtcUrl}`);
            const response = await fetch(go2rtcUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    type: 'offer',
                    sdp: pc.localDescription.sdp
                })
            });

            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`go2rtc WebRTC request failed: ${response.status} - ${errorText}`);
            }

            // go2rtc returns JSON with SDP answer
            const answer = await response.json();
            await pc.setRemoteDescription({
                type: 'answer',
                sdp: answer.sdp
            });

            // Store stream info (reuse same map as MediaMTX WebRTC)
            this.activeStreams.set(cameraId, {
                pc: pc,
                element: videoElement,
                type: 'sub',  // go2rtc serves native resolution (no sub/main distinction)
                startTime: Date.now(),
                source: 'go2rtc'  // Tag to distinguish from MediaMTX WebRTC
            });

            // Attach latency meter (green badge, same as MediaMTX WebRTC)
            this._attachLatencyMeter(pc, videoElement);

            this.retryAttempts.delete(cameraId);
            console.log(`[go2rtc] ${cameraId}: Stream setup complete, waiting for media`);
            return true;

        } catch (error) {
            console.error(`[go2rtc] Failed to start stream for ${cameraId}:`, error);

            // Clean up on failure
            const stream = this.activeStreams.get(cameraId);
            if (stream?.pc) {
                stream.pc.close();
            }
            this.activeStreams.delete(cameraId);
            throw error;
        }
    }

    /**
     * Get WebRTC connection statistics for a camera
     */
    async getStats(cameraId) {
        const stream = this.activeStreams.get(cameraId);
        if (!stream?.pc) return null;

        try {
            const stats = await stream.pc.getStats();
            const result = {
                video: null,
                audio: null,
                connection: null
            };

            stats.forEach(report => {
                if (report.type === 'inbound-rtp') {
                    if (report.kind === 'video') {
                        result.video = {
                            packetsReceived: report.packetsReceived,
                            packetsLost: report.packetsLost,
                            bytesReceived: report.bytesReceived,
                            framesDecoded: report.framesDecoded,
                            framesDropped: report.framesDropped,
                            jitter: report.jitter
                        };
                    } else if (report.kind === 'audio') {
                        result.audio = {
                            packetsReceived: report.packetsReceived,
                            packetsLost: report.packetsLost,
                            bytesReceived: report.bytesReceived,
                            jitter: report.jitter
                        };
                    }
                } else if (report.type === 'candidate-pair' && report.state === 'succeeded') {
                    result.connection = {
                        localCandidateType: report.localCandidateType,
                        remoteCandidateType: report.remoteCandidateType,
                        currentRoundTripTime: report.currentRoundTripTime,
                        availableOutgoingBitrate: report.availableOutgoingBitrate
                    };
                }
            });

            return result;
        } catch (error) {
            console.error(`[WebRTC] Failed to get stats for ${cameraId}:`, error);
            return null;
        }
    }
}
