/**
 * Logger Module - ES6 + jQuery
 * Handles activity logging with console integration
 */

export class Logger {
    constructor(maxEntries = 50) {
        this.maxEntries = maxEntries;
    }

    log(message, type = 'info') {
        const timestamp = new Date().toLocaleTimeString();
        const $entry = this.createLogEntry(timestamp, message, type);

        // Add to DOM
        $('#activity-log').append($entry);

        // Remove old entries if we exceed max
        this.trimEntries();

        // Scroll to bottom
        this.scrollToBottom();

        // Also log to console
        this.consoleLog(message, type);
    }

    createLogEntry(timestamp, message, type) {
        return $(`
            <div class="log-entry ${type}">
                <span class="timestamp">${timestamp}</span>
                <span class="message">${message}</span>
            </div>
        `);
    }

    trimEntries() {
        const $entries = $('#activity-log .log-entry');
        if ($entries.length > this.maxEntries) {
            $entries.slice(0, $entries.length - this.maxEntries).remove();
        }
    }

    scrollToBottom() {
        const $log = $('#activity-log');
        if ($log.length) {
            $log.scrollTop($log[0].scrollHeight);
        }
    }

    consoleLog(message, type) {
        const prefix = `[${new Date().toLocaleTimeString()}]`;

        switch (type) {
            case 'success':
                console.log(`%c${prefix} ✓ ${message}`, 'color: green; font-weight: bold');
                break;
            case 'error':
                console.error(`${prefix} ✗ ${message}`);
                break;
            case 'warning':
                console.warn(`${prefix} ⚠ ${message}`);
                break;
            default:
                console.log(`${prefix} ${message}`);
        }
    }

    // Convenience methods
    info(message) {
        this.log(message, 'info');
    }

    success(message) {
        this.log(message, 'success');
    }

    error(message) {
        this.log(message, 'error');
    }

    warning(message) {
        this.log(message, 'warning');
    }
}
