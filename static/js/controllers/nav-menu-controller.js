/**
 * Navigation Menu Controller
 *
 * Handles the slide-in navigation menu (hamburger menu):
 * - Open/close the slide-in drawer
 * - Proxy clicks from menu items to their corresponding actions
 * - Close on overlay tap or Escape key
 *
 * @module controllers/nav-menu-controller
 */

class NavMenuController {
    constructor() {
        /* DOM elements */
        this.$hamburgerBtn = $('#navbar-hamburger-btn');
        this.$navMenu = $('#nav-menu');
        this.$overlay = $('#nav-menu-overlay');
        this.$closeBtn = $('#nav-menu-close');

        /* State */
        this.isOpen = false;

        this._init();
    }

    /**
     * Initialize the controller
     */
    _init() {
        console.log('[NavMenu] Initializing...');
        this._setupEventListeners();
    }

    /**
     * Set up all event listeners
     */
    _setupEventListeners() {
        /* Hamburger button toggles menu */
        this.$hamburgerBtn.on('click', () => this.toggle());

        /* Close button inside menu */
        this.$closeBtn.on('click', () => this.close());

        /* Overlay click closes menu */
        this.$overlay.on('click', () => this.close());

        /* Escape key closes menu */
        $(document).on('keydown', (e) => {
            if (e.key === 'Escape' && this.isOpen) {
                this.close();
            }
        });

        /* Menu item: Camera Selector - triggers the existing camera selector button */
        $('#menu-camera-selector').on('click', () => {
            this.close();
            $('#camera-selector-btn').trigger('click');
        });

        /* Menu item: Settings - triggers the existing settings button */
        $('#menu-settings-btn').on('click', () => {
            this.close();
            $('#settings-btn').trigger('click');
        });

        console.log('[NavMenu] Event listeners attached');
    }

    /**
     * Toggle the menu open/closed
     */
    toggle() {
        if (this.isOpen) {
            this.close();
        } else {
            this.open();
        }
    }

    /**
     * Open the slide-in menu
     */
    open() {
        this.$navMenu.addClass('open');
        this.$overlay.addClass('show');
        this.isOpen = true;
        console.log('[NavMenu] Menu opened');
    }

    /**
     * Close the slide-in menu
     */
    close() {
        this.$navMenu.removeClass('open');
        this.$overlay.removeClass('show');
        this.isOpen = false;
        console.log('[NavMenu] Menu closed');
    }
}

/* Initialize when DOM is ready */
$(document).ready(() => {
    window.navMenuController = new NavMenuController();
});

export default NavMenuController;
