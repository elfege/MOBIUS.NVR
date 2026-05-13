/**
 * Host-agent install UI — Phase 2c.
 *
 * Renders two cards inside the existing Performance settings tab:
 *
 *   1. "Install on this machine" — detects the browser's OS client-side
 *      from navigator.userAgent and, if Linux, fetches the curl one-liner
 *      from /api/host-agent/install-command for the currently-bound
 *      host_label. Non-Linux UAs see a greyed button with a tooltip
 *      pulled from /api/host-agent/compatibility (so the missing-port
 *      TODO surfaces directly in the UI, not just in this codebase).
 *
 *   2. "Install on another LAN machine" — text input for the target's
 *      label + an OS dropdown. Submit hits the same two endpoints,
 *      gated on the chosen OS's compatibility.
 *
 * Both cards open a modal that displays the curl one-liner in a terminal-
 * styled pre block with a prominent Copy button. The operator pastes that
 * single line into the target machine's terminal and the host-agent
 * installs itself.
 *
 * The "current label" is read from localStorage.mobius_host_label — the
 * SAME key the performance-throttle module writes when the operator
 * presses Bind. If unset, the "this machine" card shows a disabled state
 * pointing the operator at the Host Label binding above.
 */

// localStorage key shared with performance-throttle.js / visibility-manager.
const LS_KEY = 'mobius_host_label';

// Same regex the server enforces in routes/host_agent_install.py. Mirrored
// here so we can fail fast on the client without a round trip.
const LABEL_RE = /^[a-z][a-z0-9_-]*$/;

/**
 * Detect the browser's OS from navigator.userAgent. Returns one of
 * 'linux' | 'darwin' | 'windows' | 'unknown'. The matcher order matters:
 * iPad/iPhone fall under 'unknown' because we cannot run a host-agent on
 * iOS; Android likewise. The host-agent target is a desktop kiosk.
 */
function detectOS() {
    const ua = navigator.userAgent || '';
    if (/Android/i.test(ua)) return 'unknown';
    if (/iPad|iPhone|iPod/i.test(ua)) return 'unknown';
    if (/Mac OS X|Macintosh/i.test(ua)) return 'darwin';
    if (/Windows/i.test(ua)) return 'windows';
    if (/Linux|X11|CrOS/i.test(ua)) return 'linux';
    return 'unknown';
}

/**
 * Read the host_label the operator has bound on this browser. Same source
 * as performance-throttle.resolveHostLabel().
 */
function currentLabel() {
    try {
        return (localStorage.getItem(LS_KEY) || '').trim();
    } catch (_) {
        return '';
    }
}

/**
 * Fetch the compatibility verdict for an OS string. Returns the parsed
 * JSON shape: { os, compatible, reason, todo }. Falls back to a synthetic
 * 'unknown' response if the network call fails so the UI never gets stuck.
 */
async function fetchCompatibility(osKey) {
    try {
        const r = await fetch(
            `/api/host-agent/compatibility?os=${encodeURIComponent(osKey)}`,
            { credentials: 'same-origin' }
        );
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return await r.json();
    } catch (e) {
        console.warn('[HostAgentInstall] compatibility fetch failed:', e);
        return {
            os: 'unknown',
            compatible: false,
            reason: `compatibility check failed (${e.message || e})`,
            todo: null,
        };
    }
}

/**
 * Fetch the install command for a given label. Returns the parsed shape
 * { label, command, server_url[, warning] } or throws on error.
 */
async function fetchInstallCommand(label) {
    const r = await fetch(
        `/api/host-agent/install-command?label=${encodeURIComponent(label)}`,
        { credentials: 'same-origin' }
    );
    if (!r.ok) {
        let detail = '';
        try {
            const j = await r.json();
            if (j && j.error) detail = `: ${j.error}`;
        } catch (_) {}
        throw new Error(`HTTP ${r.status}${detail}`);
    }
    return await r.json();
}

/**
 * Show the install-command modal. Builds a one-shot overlay on demand so
 * we don't pollute the DOM with permanent hidden nodes. The user can copy
 * with the button or Ctrl/Cmd+C after selecting the text.
 */
function showInstallModal({ label, command, warning }) {
    // Remove any prior modal — single-flight UX, repeated clicks just
    // re-open the modal with fresh data.
    $('#host-agent-install-modal').remove();

    const $modal = $(`
        <div id="host-agent-install-modal"
             style="position:fixed;inset:0;background:rgba(0,0,0,0.65);
                    z-index:10001;display:flex;align-items:center;
                    justify-content:center;">
            <div style="background:#1e1e1e;color:#e0e0e0;border:1px solid #444;
                        border-radius:8px;max-width:760px;width:90%;
                        max-height:80vh;overflow-y:auto;padding:22px;">
                <div style="display:flex;justify-content:space-between;
                            align-items:center;margin-bottom:12px;">
                    <h3 style="margin:0;color:#fff;">
                        <i class="fas fa-terminal"></i>
                        Install host-agent on
                        <code style="color:#7ec8ff;">${label}</code>
                    </h3>
                    <button class="hai-modal-close"
                            style="background:transparent;border:0;color:#aaa;
                                   font-size:22px;cursor:pointer;">&times;</button>
                </div>

                <p style="margin:8px 0 14px;color:#ccc;font-size:13px;">
                    Paste this one-liner into a terminal on the target
                    machine. It downloads the install script over HTTPS
                    and runs it. Re-running on a machine that already has
                    the agent is idempotent (the systemd unit is refreshed
                    and the existing config is preserved).
                </p>

                ${warning ? `
                    <div style="background:#3a2e10;border:1px solid #6b5717;
                                color:#ffd66b;padding:10px;border-radius:6px;
                                margin-bottom:14px;font-size:12px;">
                        <i class="fas fa-exclamation-triangle"></i>
                        ${warning}
                    </div>
                ` : ''}

                <pre id="hai-modal-cmd"
                     style="background:#0d0d0d;color:#bef58c;border:1px solid #333;
                            padding:14px;border-radius:6px;
                            font-family:'JetBrains Mono', Menlo, Consolas, monospace;
                            font-size:13px;white-space:pre-wrap;word-break:break-all;
                            user-select:all;margin:0 0 14px;">${
                                $('<div>').text(command).html()
                            }</pre>

                <div style="display:flex;gap:10px;justify-content:flex-end;">
                    <button class="hai-modal-copy setting-btn setting-btn-primary"
                            style="font-size:13px;padding:7px 16px;">
                        <i class="fas fa-copy"></i> Copy
                    </button>
                    <button class="hai-modal-close setting-btn setting-btn-secondary"
                            style="font-size:13px;padding:7px 16px;">
                        Close
                    </button>
                </div>

                <div class="hai-modal-feedback"
                     style="margin-top:10px;font-size:12px;color:#7ec8ff;
                            min-height:1em;text-align:right;"></div>
            </div>
        </div>
    `);

    $modal.on('click', (e) => {
        // Click on backdrop (modal itself, not the inner box) closes.
        if (e.target === $modal[0]) $modal.remove();
    });
    $modal.find('.hai-modal-close').on('click', () => $modal.remove());

    $modal.find('.hai-modal-copy').on('click', async () => {
        const $fb = $modal.find('.hai-modal-feedback');
        try {
            // Prefer the async Clipboard API; fall back to execCommand for
            // older browsers and for the "permissions not granted" case.
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(command);
            } else {
                const $pre = $modal.find('#hai-modal-cmd');
                const range = document.createRange();
                range.selectNodeContents($pre[0]);
                const sel = window.getSelection();
                sel.removeAllRanges();
                sel.addRange(range);
                document.execCommand('copy');
                sel.removeAllRanges();
            }
            $fb.css('color', '#bef58c').text('Copied to clipboard.');
        } catch (e) {
            $fb.css('color', '#ff8a8a').text(`Copy failed: ${e.message || e}`);
        }
    });

    $('body').append($modal);
}


export const hostAgentInstall = {
    /**
     * HTML fragment for the two install cards. Designed to be APPENDED
     * to the existing Performance tab's content (inside the same
     * .settings-tab-panel[data-tab-panel="performance"]), so the styling
     * hooks (.setting-row, .setting-top, .setting-control, …) match the
     * rest of the panel.
     */
    renderHTML() {
        return `
        <div class="setting-row" id="hai-this-machine-row">
            <div class="setting-top">
                <div class="setting-label">
                    <i class="fas fa-desktop"></i> Install host-agent on this machine
                </div>
                <div class="setting-control">
                    <button id="hai-this-machine-btn"
                            class="setting-btn setting-btn-primary"
                            style="font-size:12px;padding:5px 12px;"
                            disabled>
                        <i class="fas fa-download"></i> Show install command
                    </button>
                </div>
            </div>
            <div class="setting-description" id="hai-this-machine-desc">
                Detecting this machine's OS…
            </div>
        </div>

        <div class="setting-row" id="hai-other-machine-row">
            <div class="setting-top">
                <div class="setting-label">
                    <i class="fas fa-network-wired"></i> Install on another LAN machine
                </div>
                <div class="setting-control" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
                    <input type="text" id="hai-other-label" class="setting-input"
                           placeholder="target host_label (e.g. rog)"
                           style="width:180px;">
                    <select id="hai-other-os" class="setting-input" style="width:120px;">
                        <option value="linux">Linux</option>
                        <option value="darwin">Mac</option>
                        <option value="windows">Windows</option>
                        <option value="unknown">Unknown</option>
                    </select>
                    <button id="hai-other-go"
                            class="setting-btn setting-btn-primary"
                            style="font-size:12px;padding:5px 12px;">
                        <i class="fas fa-download"></i> Show install command
                    </button>
                </div>
            </div>
            <div class="setting-description" id="hai-other-desc">
                Type the host_label you want the target machine to report
                under (must match the regex
                <code>^[a-z][a-z0-9_-]*$</code>), pick its OS, then submit.
                Currently only Linux targets have a working install path —
                Mac and Windows ports are tracked in
                <code>docs/TODO.md</code>.
            </div>
        </div>`;
    },

    /**
     * Wire handlers. Idempotent — safe to call every time the Performance
     * tab is opened. Reads the current host_label / OS each time so the
     * UI reflects whatever the operator just typed into the Bind field.
     *
     * @param {jQuery} $panel - the .settings-tab-panel for performance
     */
    async init($panel) {
        if (!$panel || !$panel.length) return;

        // ── "This machine" card ─────────────────────────────────────────
        const osKey = detectOS();
        const compat = await fetchCompatibility(osKey);
        const label = currentLabel();

        const $thisBtn = $panel.find('#hai-this-machine-btn');
        const $thisDesc = $panel.find('#hai-this-machine-desc');

        if (!label) {
            // No host_label bound on this browser yet — the Bind field
            // above is the prerequisite. Disable, prompt the operator.
            $thisBtn.prop('disabled', true)
                    .attr('title', 'Set a Host Label above first.');
            $thisDesc.html(
                'Detected OS: <code>' + osKey + '</code>. ' +
                '<strong>Bind a host label above first.</strong>'
            );
        } else if (!compat.compatible) {
            // OS-incompatible: button greyed, hover shows reason + todo
            // so the operator knows exactly which port file is missing.
            const tip = compat.reason +
                        (compat.todo ? `\nTODO: ${compat.todo}` : '');
            $thisBtn.prop('disabled', true).attr('title', tip);
            $thisDesc.html(
                `Detected OS: <code>${osKey}</code>. ` +
                `<span style="color:#ffb84d;">` +
                `Install path not yet implemented — ${compat.reason}.` +
                `</span>` +
                (compat.todo
                    ? ` <span style="color:#888;">(${compat.todo})</span>`
                    : '')
            );
        } else {
            // Linux + label bound → enable and wire the click.
            $thisBtn.prop('disabled', false).removeAttr('title');
            $thisDesc.html(
                `Detected OS: <code>${osKey}</code>. ` +
                `Will install agent with host_label <code>${label}</code>.`
            );
            $thisBtn.off('click.hai').on('click.hai', async () => {
                try {
                    const j = await fetchInstallCommand(label);
                    showInstallModal({
                        label: j.label,
                        command: j.command,
                        warning: j.warning,
                    });
                } catch (e) {
                    alert(`Failed to build install command: ${e.message || e}`);
                }
            });
        }

        // ── "Another machine" card ──────────────────────────────────────
        const $otherGo = $panel.find('#hai-other-go');
        $otherGo.off('click.hai').on('click.hai', async () => {
            const tgtLabel = ($panel.find('#hai-other-label').val() || '').trim();
            const tgtOS = ($panel.find('#hai-other-os').val() || 'unknown').trim();

            if (!LABEL_RE.test(tgtLabel)) {
                alert(
                    'Invalid host_label.\n\n' +
                    'Must start with a lowercase letter and contain only ' +
                    'lowercase letters, digits, "-" or "_".'
                );
                return;
            }

            // OS gate — fetch compatibility for the SELECTED OS, not the
            // browser's. This is the whole point of the dropdown: the
            // operator is targeting a remote machine that may run a
            // different OS than the browser they're currently in.
            const tgtCompat = await fetchCompatibility(tgtOS);
            if (!tgtCompat.compatible) {
                const todoLine = tgtCompat.todo
                    ? `\n\nMissing port: ${tgtCompat.todo}`
                    : '';
                alert(
                    `Install not yet supported on ${tgtOS}.\n\n` +
                    `Reason: ${tgtCompat.reason}${todoLine}`
                );
                return;
            }

            try {
                const j = await fetchInstallCommand(tgtLabel);
                showInstallModal({
                    label: j.label,
                    command: j.command,
                    warning: j.warning,
                });
            } catch (e) {
                alert(`Failed to build install command: ${e.message || e}`);
            }
        });
    },
};
