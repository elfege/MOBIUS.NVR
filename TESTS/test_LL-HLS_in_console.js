// The JS below is to be pasted in the console directly. 

/**
 * The command below publishes INTO the running container. 
 * 
  CON_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' nvr-packager)
    
  ffmpeg -rtsp_transport tcp -i rtsp://admin:xxxxxxxxxxxxxxxxxxxxxxx@192.168.10.88:554/h264Preview_01_sub   -c:v libx264 -preset veryfast -tune zerolatency   -g 15 -keyint_min 15 -force_key_frames "expr:gte(t,n_forced*1)"   -x264-params "scenecut=0:min-keyint=15:open_gop=0"   -b:v 800k -maxrate 800k -bufsize 1600k -pix_fmt yuv420p -profile:v baseline   -c:a aac -b:a 64k -ar 44100 -ac 1   -f flv rtmp://$CON_IP:1935/REOLINK_OFFICE
  
/*
 CON_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' nvr-packager)

 # publish video-only with 1 s GOP to MediaMTX (single line)

ffmpeg -loglevel warning -rtsp_transport tcp -i rtsp://admin:xxxxxxxxxxxxxxxxxxxxxxx@192.168.10.88:554/h264Preview_01_sub -an -c:v libx264 -preset veryfast -tune zerolatency -g 15 -keyint_min 15 -force_key_frames "expr:gte(t,n_forced*1)" -x264-params "scenecut=0:min-keyint=15:open_gop=0" -b:v 800k -maxrate 800k -bufsize 1600k -pix_fmt yuv420p -profile:v baseline -f flv rtmp://$CON_IP:1935/REOLINK_OFFICE


*/

//   Once you ran the command, execute this inside the U.I.'s console: 



(() => {
  const src = `${window.location.origin}/hls/REOLINK_OFFICE/index.m3u8`;

  // reuse overlay if it exists
  const v = window._llv || Object.assign(document.createElement('video'), {
    muted:true, autoplay:true, playsInline:true, controls:true,
    style:'position:fixed;right:12px;bottom:12px;width:360px;z-index:999999'
  });
  if (!window._llv) document.body.appendChild(v);

  if (window._llhls) { try { window._llhls.destroy(); } catch(e){} }

  const h = new Hls({
    lowLatencyMode: true,
    targetLatencySec: 1.2,         // ≈ PART-HOLD-BACK(≈1.0) + margin
    liveSyncDuration: 0.6,         // hug the edge
    liveMaxLatencyDuration: 1.8,   // cap drift
    maxLiveSyncPlaybackRate: 2.5,  // faster catch-up if behind
    backBufferLength: 10,
    maxFragLookUpTolerance: 0.0
  });

  h.loadSource(src);
  h.attachMedia(v);
  h.on(Hls.Events.ERROR, (e, d) => console.warn('[LL-HLS]', d?.details || e, d));
  window._llhls = h; window._llv = v;
})();


// with different values
(() => {
  const src = `${window.location.origin}/hls/REOLINK_OFFICE/index.m3u8`;
  if (window._llhls) { try { window._llhls.destroy(); } catch(e){} }
  const v = window._llv || Object.assign(document.createElement('video'), {
    muted:true, autoplay:true, playsInline:true, controls:true,
    style:'position:fixed;right:12px;bottom:12px;width:360px;z-index:999999'
  }); if (!window._llv) document.body.appendChild(v);
  const h = new Hls({
    lowLatencyMode: true,
    targetLatencySec: 1.2,
    liveSyncDuration: 0.6,
    liveMaxLatencyDuration: 1.8,
    maxLiveSyncPlaybackRate: 2.5,
    backBufferLength: 10,
    maxFragLookUpTolerance: 0.0
  });
  h.loadSource(src); h.attachMedia(v);
  h.on(Hls.Events.ERROR, (e,d)=>console.warn('[LL-HLS]', d?.details||e, d));
  window._llhls=h; window._llv=v;
})();


(async () => {
  const base = window.location.origin;
  const variant = `${base}/hls/REOLINK_OFFICE/video1_stream.m3u8`;
  const txt = await (await fetch(variant, { cache: 'no-store' })).text();

  const sc = txt.match(/^#EXT-X-SERVER-CONTROL:(.*)$/m)?.[1] || '';
  const phb = +(/PART-HOLD-BACK=([\d.]+)/.exec(sc)?.[1] || NaN);      // seconds
  const pti = +(/^#EXT-X-PART-INF:PART-TARGET=([\d.]+)/m.exec(txt)?.[1] || NaN);

  console.log('SERVER-CONTROL:', sc || '(none)');
  console.log('PART-HOLD-BACK:', phb, 's', ' | PART-TARGET:', pti, 's');

  // (re)build the overlay player with tuned targets
  const src = `${base}/hls/REOLINK_OFFICE/index.m3u8`;
  if (window._llhls) { try { window._llhls.destroy(); } catch(e){} }
  const v = window._llv || Object.assign(document.createElement('video'), {
    muted:true, autoplay:true, playsInline:true, controls:true,
    style:'position:fixed;right:12px;bottom:12px;width:360px;z-index:999999'
  });
  if (!window._llv) document.body.appendChild(v);

  // Fallbacks if tags are missing
  const targetLatencySec = (isFinite(phb) ? phb + 0.20 : 1.2);  // stay just ahead of hold-back
  const liveSyncDuration  = (isFinite(pti) ? Math.max(0.3, pti * 1.2) : 0.6);
  const liveMaxLatency    = targetLatencySec + (isFinite(pti) ? pti * 2 : 0.6);

  const h = new Hls({
    lowLatencyMode: true,
    targetLatencySec,
    liveSyncDuration:  liveSyncDuration,
    liveMaxLatencyDuration: liveMaxLatency,
    maxLiveSyncPlaybackRate: 3.0,
    backBufferLength: 8,
    maxFragLookUpTolerance: 0.0
  });

  h.loadSource(src);
  h.attachMedia(v);
  h.on(Hls.Events.ERROR, (_e, d) => console.warn('[LL-HLS]', d?.details || d));
  window._llhls = h; window._llv = v;

  console.log('Applied:', { targetLatencySec, liveSyncDuration, liveMaxLatency });
})();

