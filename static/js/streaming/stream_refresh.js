// static/js/streaming/stream_refresh.js
// ES6 module. No renames to your existing maps; they’re passed in.

// NOT CURRENTLY IN USE

export class StreamRefresher {
  /**
   * @param {Object} deps
   * @param {Map}    deps.hlsInstances - same map you already use
   * @param {Map}    deps.activeStreams - same map you already use
   * @param {Function} deps.startStream - (cameraId, videoElement, streamType) => Promise
   */
  constructor({ hlsInstances, activeStreams, startStream }) {
    this.hlsInstances = hlsInstances;
    this.activeStreams = activeStreams;
    this.startStream = startStream;
  }

  /**
   * Force refresh a stream by stopping/starting on backend, then reattaching.
   * Protocol-agnostic: your existing startStream decides HLS/RTMP/MJPEG.
   */
  async forceRefreshStream(cameraId, videoElement) {
    // 0) Remember current type (default to 'sub') before we clear the map
    const current = this.activeStreams.get(cameraId);
    const streamType = current?.type ?? 'sub';

    // 1) Client-side teardown (no renames)
    try {
      const existingHls = this.hlsInstances.get(cameraId);
      if (existingHls) {
        existingHls.destroy();
        this.hlsInstances.delete(cameraId);
      }
    } catch (e) {
      console.warn(`[forceRefreshStream] HLS teardown warning for ${cameraId}:`, e);
    }

    const stream = this.activeStreams.get(cameraId);
    if (stream && stream.element) {
      try {
        stream.element.src = '';
        stream.element.load?.();
      } catch (e) {
        console.warn(`[forceRefreshStream] element reset warning for ${cameraId}:`, e);
      }
      this.activeStreams.delete(cameraId);
    }

    // 2) Tell backend to STOP
    try {
      const res = await fetch(`/api/stream/stop/${encodeURIComponent(cameraId)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });
      if (!res.ok) console.warn(`[forceRefreshStream] stop returned ${res.status} for ${cameraId}`);
    } catch (e) {
      console.warn(`[forceRefreshStream] stop failed for ${cameraId}:`, e);
    }

    // 3) Poll /status until backend reports fully down to avoid “already active”
    const deadline = Date.now() + 5000; // up to 5s
    while (Date.now() < deadline) {
      try {
        const r = await fetch(`/api/stream/status/${encodeURIComponent(cameraId)}`);
        if (r.ok) {
          const s = await r.json();
          if (!s.is_streaming) break;
        }
      } catch (_) { /* ignore transient errors */ }
      await new Promise(r => setTimeout(r, 200));
    }

    // 4) Small grace so ffmpeg releases sockets
    await new Promise(r => setTimeout(r, 250));

    // 5) START on backend; then reattach via your existing API
    try {
      const startRes = await fetch(`/api/stream/start/${encodeURIComponent(cameraId)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: streamType, reason: 'force-refresh' })
      });
      if (!startRes.ok) {
        console.warn(`[forceRefreshStream] start returned ${startRes.status} for ${cameraId}`);
      }
    } catch (e) {
      console.warn(`[forceRefreshStream] start failed for ${cameraId}:`, e);
    }

    // 6) Give the new playlist/producer a moment, then reattach
    await new Promise(r => setTimeout(r, 500));
    return await this.startStream(cameraId, videoElement, streamType);
  }
}


