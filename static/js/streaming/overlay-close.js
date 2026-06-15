/**
 * overlay-close.js
 *
 * Click handler for the .overlay-close-btn buttons embedded in
 * .stream-controls and .ptz-controls overlays. The handler delegates to the
 * existing toggle button (data-toggle-btn selector) so the close path reuses
 * the same teardown the toggle already runs — keeping .ptz-active /
 * .controls-active classes in sync with .ptz-visible / .stream-controls-visible.
 *
 * Operator directive 2026-06-13: "need exit X for overlays such as PTZ
 * otherwise can't exit them" — particularly important in fullscreen mode
 * where the controls-toggle button in the action bar can be off-screen.
 */
(function () {
    'use strict';

    document.addEventListener('click', function (e) {
        const closeBtn = e.target.closest('.overlay-close-btn');
        if (!closeBtn) return;

        // Stop bubbling so the click doesn't also hit the underlying overlay or
        // the stream-item's own click-to-expand handler.
        e.stopPropagation();
        e.preventDefault();

        const tile = closeBtn.closest('.stream-item');
        if (!tile) return;

        const toggleSel = closeBtn.dataset.toggleBtn;
        if (!toggleSel) return;

        const toggleBtn = tile.querySelector(toggleSel);
        if (!toggleBtn) return;

        // Trigger the existing toggle handler — it knows how to close cleanly
        // (removes .ptz-visible/.stream-controls-visible AND the active class
        // on the toggle button). Avoids drifting two state-tracking paths.
        toggleBtn.click();
    }, true);  // capture phase so we beat any other handler on the close-X
})();
