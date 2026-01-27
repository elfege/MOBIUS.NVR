/**
 * File Browser Modal
 * Location: ~/0_NVR/static/js/modals/file-browser-modal.js
 * Version: 2026-01-27-v1
 *
 * Provides file browsing for alternate recording sources:
 * - Browse directories within /recordings/ALTERNATE
 * - Preview and play video files directly
 * - Navigate folder hierarchy
 */

console.log('[FileBrowser] JS file loaded - version 2026-01-27-v1');

export class FileBrowserModal {
    constructor() {
        // DOM elements
        this.$modal = null;
        this.$fileList = null;
        this.$currentPath = null;
        this.$previewSection = null;
        this.$previewVideo = null;

        // State
        this.currentPath = '/';
        this.isLoading = false;

        this.init();
    }

    /**
     * Initialize modal
     */
    init() {
        this.$modal = $('#file-browser-modal');

        if (!this.$modal.length) {
            console.warn('[FileBrowser] Modal not found in DOM');
            return;
        }

        this.$fileList = $('#file-browser-list');
        this.$currentPath = $('#current-path');
        this.$previewSection = $('#file-preview-section');
        this.$previewVideo = $('#file-preview-video');

        this.attachEvents();
        console.log('[FileBrowser] Modal initialized');
    }

    /**
     * Attach event handlers
     */
    attachEvents() {
        // Open button (in timeline modal)
        $(document).on('click', '#timeline-alternate-source-btn', () => {
            this.show();
        });

        // Close button
        this.$modal.on('click', '.file-browser-modal-close', () => {
            this.hide();
        });

        // Close on backdrop click
        this.$modal.on('click', (e) => {
            if ($(e.target).is(this.$modal)) {
                this.hide();
            }
        });

        // Close on escape key
        $(document).on('keydown', (e) => {
            if (e.key === 'Escape' && this.$modal.is(':visible')) {
                this.hide();
            }
        });

        // Navigate up button
        this.$modal.on('click', '#path-up-btn', () => {
            this.navigateUp();
        });

        // File/folder click - use delegation
        this.$fileList.on('click', 'li', (e) => {
            const $item = $(e.currentTarget);
            const type = $item.data('type');
            const name = $item.data('name');

            if (type === 'directory') {
                this.navigateToDirectory(name);
            } else if (type === 'video') {
                this.playVideo(name);
            }
        });

        // Close preview button
        this.$modal.on('click', '#file-preview-close-btn', () => {
            this.closePreview();
        });
    }

    /**
     * Show the modal and load root directory
     */
    show() {
        this.$modal.css('display', 'flex');
        this.currentPath = '/';
        this.loadDirectory('/');
    }

    /**
     * Hide the modal
     */
    hide() {
        this.$modal.hide();
        this.closePreview();
    }

    /**
     * Load directory contents
     * @param {string} path - Relative path to load
     */
    async loadDirectory(path) {
        if (this.isLoading) return;
        this.isLoading = true;

        // Update current path display
        this.currentPath = path;
        this.$currentPath.text('/recordings/ALTERNATE' + path);

        // Show loading state
        this.$fileList.hide();
        this.$modal.find('.file-browser-empty').hide();
        this.$modal.find('.file-browser-loading').show();

        try {
            const response = await fetch(`/api/files/browse?path=${encodeURIComponent(path)}`);
            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.error || 'Failed to load directory');
            }

            this.renderFileList(data.directories, data.files);
        } catch (error) {
            console.error('[FileBrowser] Error loading directory:', error);
            this.showError(error.message);
        } finally {
            this.isLoading = false;
            this.$modal.find('.file-browser-loading').hide();
        }
    }

    /**
     * Render file list
     * @param {Array} directories - List of directories
     * @param {Array} files - List of files
     */
    renderFileList(directories, files) {
        this.$fileList.empty();

        if (directories.length === 0 && files.length === 0) {
            this.$modal.find('.file-browser-empty').show();
            return;
        }

        // Add directories first
        directories.forEach(dir => {
            const modDate = new Date(dir.modified * 1000).toLocaleDateString();
            const $item = $(`
                <li data-type="directory" data-name="${this.escapeHtml(dir.name)}">
                    <i class="fas fa-folder"></i>
                    <span class="file-name">${this.escapeHtml(dir.name)}</span>
                    <div class="file-meta">
                        <span class="file-date">${modDate}</span>
                    </div>
                </li>
            `);
            this.$fileList.append($item);
        });

        // Add files
        files.forEach(file => {
            const modDate = new Date(file.modified * 1000).toLocaleDateString();
            const size = this.formatFileSize(file.size);
            const $item = $(`
                <li data-type="video" data-name="${this.escapeHtml(file.name)}">
                    <i class="fas fa-file-video"></i>
                    <span class="file-name">${this.escapeHtml(file.name)}</span>
                    <div class="file-meta">
                        <span class="file-size">${size}</span>
                        <span class="file-date">${modDate}</span>
                    </div>
                </li>
            `);
            this.$fileList.append($item);
        });

        this.$fileList.show();
    }

    /**
     * Navigate to a subdirectory
     * @param {string} dirName - Directory name to navigate to
     */
    navigateToDirectory(dirName) {
        const newPath = this.currentPath === '/'
            ? '/' + dirName
            : this.currentPath + '/' + dirName;
        this.loadDirectory(newPath);
    }

    /**
     * Navigate up one directory level
     */
    navigateUp() {
        if (this.currentPath === '/') return;

        const parts = this.currentPath.split('/').filter(p => p);
        parts.pop();
        const newPath = parts.length === 0 ? '/' : '/' + parts.join('/');
        this.loadDirectory(newPath);
    }

    /**
     * Play a video file
     * @param {string} fileName - Name of the file to play
     */
    playVideo(fileName) {
        // Construct the file path
        const filePath = this.currentPath === '/'
            ? fileName
            : this.currentPath.substring(1) + '/' + fileName;

        // Update preview section
        $('#file-preview-name').text(fileName);

        // Set video source
        const videoUrl = `/api/files/stream/${encodeURIComponent(filePath)}`;
        this.$previewVideo.attr('src', videoUrl);
        this.$previewVideo[0].load();
        this.$previewVideo[0].play().catch(e => {
            console.log('[FileBrowser] Autoplay prevented, user must click play');
        });

        // Show preview section
        this.$previewSection.show();

        // Highlight selected item
        this.$fileList.find('li').removeClass('selected');
        this.$fileList.find(`li[data-name="${this.escapeHtml(fileName)}"]`).addClass('selected');
    }

    /**
     * Close the video preview
     */
    closePreview() {
        this.$previewVideo[0].pause();
        this.$previewVideo.attr('src', '');
        this.$previewSection.hide();
        this.$fileList.find('li').removeClass('selected');
    }

    /**
     * Show error message
     * @param {string} message - Error message to display
     */
    showError(message) {
        const $empty = this.$modal.find('.file-browser-empty');
        $empty.html(`<i class="fas fa-exclamation-triangle"></i> ${this.escapeHtml(message)}`);
        $empty.show();
    }

    /**
     * Format file size in human-readable format
     * @param {number} bytes - Size in bytes
     * @returns {string} Formatted size string
     */
    formatFileSize(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
    }

    /**
     * Escape HTML to prevent XSS
     * @param {string} text - Text to escape
     * @returns {string} Escaped text
     */
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Auto-initialize when DOM is ready
$(document).ready(() => {
    window.fileBrowserModal = new FileBrowserModal();
});
