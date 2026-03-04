/**
 * File Browser Modal
 * Location: ~/0_MOBIUS.NVR/static/js/modals/file-browser-modal.js
 * Version: 2026-01-27-v4 (fixed download URL encoding for special characters)
 *
 * Provides file browsing for alternate recording sources:
 * - Browse directories within /recordings/ALTERNATE
 * - Editable path input with error handling
 * - Preview and play video files directly
 * - Navigate folder hierarchy
 * - Multi-select files with checkboxes
 * - Download single or multiple files
 */

console.log('[FileBrowser] JS file loaded - version 2026-01-27-v4');

export class FileBrowserModal {
    constructor() {
        // DOM elements
        this.$modal = null;
        this.$fileList = null;
        this.$pathInput = null;
        this.$pathError = null;
        this.$pathErrorMsg = null;
        this.$previewSection = null;
        this.$previewVideo = null;
        this.$selectAll = null;
        this.$downloadBtn = null;
        this.$selectionCount = null;

        // State
        this.currentPath = '/';
        this.basePath = '/recordings/ALTERNATE';
        this.isLoading = false;
        this.selectedFiles = new Set();
        this.currentFiles = [];

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
        this.$pathInput = $('#current-path-input');
        this.$pathError = $('#path-error');
        this.$pathErrorMsg = $('#path-error-message');
        this.$previewSection = $('#file-preview-section');
        this.$previewVideo = $('#file-preview-video');
        this.$selectAll = $('#file-select-all');
        this.$downloadBtn = $('#file-download-btn');
        this.$selectionCount = $('#file-selection-count');

        this.attachEvents();
        console.log('[FileBrowser] Modal initialized with editable path');
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

        // Go button - navigate to entered path
        this.$modal.on('click', '#path-go-btn', () => {
            this.navigateToInputPath();
        });

        // Enter key in path input
        this.$pathInput.on('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                this.navigateToInputPath();
            }
        });

        // Clear error on input change
        this.$pathInput.on('input', () => {
            this.hidePathError();
        });

        // Navigate up button
        this.$modal.on('click', '#path-up-btn', () => {
            this.navigateUp();
        });

        // File/folder click
        this.$fileList.on('click', 'li', (e) => {
            const $item = $(e.currentTarget);
            const type = $item.data('type');
            const name = $item.data('name');

            if ($(e.target).hasClass('file-checkbox')) {
                return;
            }

            if (type === 'directory') {
                this.navigateToDirectory(name);
            } else if (type === 'video') {
                this.playVideo(name);
            }
        });

        // Checkbox change handler
        this.$fileList.on('change', '.file-checkbox', (e) => {
            e.stopPropagation();
            const $checkbox = $(e.target);
            const $item = $checkbox.closest('li');
            const name = $item.data('name');

            if ($checkbox.is(':checked')) {
                this.selectedFiles.add(name);
                $item.addClass('checked');
            } else {
                this.selectedFiles.delete(name);
                $item.removeClass('checked');
            }

            this.updateSelectionUI();
        });

        // Select all checkbox
        this.$selectAll.on('change', () => {
            const isChecked = this.$selectAll.is(':checked');

            this.$fileList.find('li[data-type="video"]').each((_, el) => {
                const $item = $(el);
                const name = $item.data('name');
                const $checkbox = $item.find('.file-checkbox');

                $checkbox.prop('checked', isChecked);
                if (isChecked) {
                    this.selectedFiles.add(name);
                    $item.addClass('checked');
                } else {
                    this.selectedFiles.delete(name);
                    $item.removeClass('checked');
                }
            });

            this.updateSelectionUI();
        });

        // Download button
        this.$downloadBtn.on('click', () => {
            this.downloadSelected();
        });

        // Close preview button
        this.$modal.on('click', '#file-preview-close-btn', () => {
            this.closePreview();
        });
    }

    /**
     * Navigate to the path entered in the input field
     */
    navigateToInputPath() {
        let inputPath = this.$pathInput.val().trim();

        // Handle empty input
        if (!inputPath) {
            inputPath = this.basePath + '/';
        }

        // Convert full path to relative path
        let relativePath = inputPath;
        if (inputPath.startsWith(this.basePath)) {
            relativePath = inputPath.substring(this.basePath.length);
        }

        // Normalize: ensure starts with /
        if (!relativePath.startsWith('/')) {
            relativePath = '/' + relativePath;
        }

        // Remove trailing slash for consistency (except root)
        if (relativePath.length > 1 && relativePath.endsWith('/')) {
            relativePath = relativePath.slice(0, -1);
        }

        console.log(`[FileBrowser] Navigating to: ${relativePath}`);
        this.loadDirectory(relativePath);
    }

    /**
     * Show path error message
     * @param {string} message - Error message to display
     */
    showPathError(message) {
        this.$pathInput.addClass('error');
        this.$pathErrorMsg.text(message);
        this.$pathError.show();
    }

    /**
     * Hide path error message
     */
    hidePathError() {
        this.$pathInput.removeClass('error');
        this.$pathError.hide();
    }

    /**
     * Update the path input field
     * @param {string} relativePath - Relative path from base
     */
    updatePathInput(relativePath) {
        const fullPath = this.basePath + relativePath;
        this.$pathInput.val(fullPath + (relativePath === '/' ? '' : '/'));
    }

    /**
     * Update selection UI
     */
    updateSelectionUI() {
        const count = this.selectedFiles.size;

        if (count === 0) {
            this.$selectionCount.text('').removeClass('has-selection');
            this.$downloadBtn.prop('disabled', true);
        } else {
            const totalSize = this.calculateSelectedSize();
            this.$selectionCount
                .text(`${count} file${count > 1 ? 's' : ''} selected (${this.formatFileSize(totalSize)})`)
                .addClass('has-selection');
            this.$downloadBtn.prop('disabled', false);
        }

        const videoCount = this.$fileList.find('li[data-type="video"]').length;
        if (videoCount > 0 && count === videoCount) {
            this.$selectAll.prop('checked', true);
            this.$selectAll.prop('indeterminate', false);
        } else if (count > 0) {
            this.$selectAll.prop('checked', false);
            this.$selectAll.prop('indeterminate', true);
        } else {
            this.$selectAll.prop('checked', false);
            this.$selectAll.prop('indeterminate', false);
        }
    }

    /**
     * Calculate total size of selected files
     */
    calculateSelectedSize() {
        let total = 0;
        this.selectedFiles.forEach(name => {
            const file = this.currentFiles.find(f => f.name === name);
            if (file) {
                total += file.size;
            }
        });
        return total;
    }

    /**
     * Download selected files
     */
    async downloadSelected() {
        if (this.selectedFiles.size === 0) return;

        const files = Array.from(this.selectedFiles);
        console.log(`[FileBrowser] Downloading ${files.length} files`);

        for (const fileName of files) {
            await this.downloadFile(fileName);
            await new Promise(resolve => setTimeout(resolve, 300));
        }
    }

    /**
     * Download a single file
     */
    async downloadFile(fileName) {
        const filePath = this.currentPath === '/'
            ? fileName
            : this.currentPath.substring(1) + '/' + fileName;

        // Encode each path segment separately to preserve slashes for Flask routing
        const downloadUrl = `/api/files/download/${filePath.split('/').map(encodeURIComponent).join('/')}`;

        const link = document.createElement('a');
        link.href = downloadUrl;
        link.download = fileName;
        link.style.display = 'none';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);

        console.log(`[FileBrowser] Started download: ${fileName}`);
    }

    /**
     * Show the modal
     */
    show() {
        this.$modal.css('display', 'flex');
        this.currentPath = '/';
        this.selectedFiles.clear();
        this.updateSelectionUI();
        this.hidePathError();
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
     */
    async loadDirectory(path) {
        if (this.isLoading) return;
        this.isLoading = true;

        // Clear selection and errors
        this.selectedFiles.clear();
        this.updateSelectionUI();
        this.hidePathError();

        // Update state and UI
        this.currentPath = path;
        this.updatePathInput(path);

        // Show loading
        this.$fileList.hide();
        this.$modal.find('.file-browser-empty').hide();
        this.$modal.find('.file-browser-loading').show();

        try {
            const response = await fetch(`/api/files/browse?path=${encodeURIComponent(path)}`);
            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.error || 'Failed to load directory');
            }

            // Check for error in response
            if (data.error) {
                throw new Error(data.error);
            }

            // Check if directory exists
            if (data.message && data.message.includes('does not exist')) {
                this.showPathError('Directory does not exist');
                this.$modal.find('.file-browser-empty').html(
                    '<i class="fas fa-folder-open"></i> Directory not found'
                ).show();
                this.currentFiles = [];
                return;
            }

            this.currentFiles = data.files;
            this.renderFileList(data.directories, data.files);

        } catch (error) {
            console.error('[FileBrowser] Error loading directory:', error);
            this.showPathError(error.message || 'Failed to load directory');
            this.$modal.find('.file-browser-empty').html(
                `<i class="fas fa-exclamation-triangle"></i> ${this.escapeHtml(error.message)}`
            ).show();
        } finally {
            this.isLoading = false;
            this.$modal.find('.file-browser-loading').hide();
        }
    }

    /**
     * Render file list
     */
    renderFileList(directories, files) {
        this.$fileList.empty();

        if (directories.length === 0 && files.length === 0) {
            this.$modal.find('.file-browser-empty').html(
                '<i class="fas fa-folder-open"></i> No files found'
            ).show();
            return;
        }

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

        files.forEach(file => {
            const modDate = new Date(file.modified * 1000).toLocaleDateString();
            const size = this.formatFileSize(file.size);
            const $item = $(`
                <li data-type="video" data-name="${this.escapeHtml(file.name)}">
                    <input type="checkbox" class="file-checkbox" title="Select for download">
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
     * Navigate to subdirectory
     */
    navigateToDirectory(dirName) {
        const newPath = this.currentPath === '/'
            ? '/' + dirName
            : this.currentPath + '/' + dirName;
        this.loadDirectory(newPath);
    }

    /**
     * Navigate up one level
     */
    navigateUp() {
        if (this.currentPath === '/') return;

        const parts = this.currentPath.split('/').filter(p => p);
        parts.pop();
        const newPath = parts.length === 0 ? '/' : '/' + parts.join('/');
        this.loadDirectory(newPath);
    }

    /**
     * Play video file
     */
    playVideo(fileName) {
        const filePath = this.currentPath === '/'
            ? fileName
            : this.currentPath.substring(1) + '/' + fileName;

        $('#file-preview-name').text(fileName);

        const videoUrl = `/api/files/stream/${encodeURIComponent(filePath)}`;
        this.$previewVideo.attr('src', videoUrl);
        this.$previewVideo[0].load();
        this.$previewVideo[0].play().catch(e => {
            console.log('[FileBrowser] Autoplay prevented');
        });

        this.$previewSection.show();

        this.$fileList.find('li').removeClass('selected');
        this.$fileList.find(`li[data-name="${this.escapeHtml(fileName)}"]`).addClass('selected');
    }

    /**
     * Close preview
     */
    closePreview() {
        this.$previewVideo[0].pause();
        this.$previewVideo.attr('src', '');
        this.$previewSection.hide();
        this.$fileList.find('li').removeClass('selected');
    }

    /**
     * Format file size
     */
    formatFileSize(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
    }

    /**
     * Escape HTML
     */
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Auto-initialize
$(document).ready(() => {
    window.fileBrowserModal = new FileBrowserModal();
});
