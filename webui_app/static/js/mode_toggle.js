/* Plan 012 Unit 5 — single/batch mode toggle.
 *
 * Replaces the 批量发布 nav tab with a toggle bar (单笔/批量). Both
 * toggle buttons use Bootstrap 5 tab API (data-bs-toggle="tab") so
 * clicking them activates #newPanel or #batchPanel via the same
 * mechanism the nav buttons used to use.
 *
 * Persistence: last-chosen mode is stored in localStorage under
 * `webui_mode_default`. On page load the saved mode is restored unless
 * the server-side hint `window.__batchTabHint` is true (batch flow
 * landed on /?batch_tab=true after batch submit).
 *
 * Gracefully degrades when localStorage is denied (private mode etc.).
 */
(function () {
    'use strict';

    var STORAGE_KEY = 'webui_mode_default';
    var DEFAULT_MODE = 'single';

    function safeGetStored() {
        try {
            return window.localStorage.getItem(STORAGE_KEY);
        } catch (_) {
            return null;
        }
    }

    function safeSetStored(value) {
        try {
            window.localStorage.setItem(STORAGE_KEY, value);
        } catch (_) {
            /* ignore */
        }
    }

    function syncToggleVisual(activeMode) {
        var singleBtn = document.getElementById('mode-single-btn');
        var batchBtn = document.getElementById('mode-batch-btn');
        if (!singleBtn || !batchBtn) return;
        singleBtn.classList.toggle('active', activeMode === 'single');
        batchBtn.classList.toggle('active', activeMode === 'batch');
    }

    function activatePane(mode) {
        var targetId = mode === 'batch' ? '#batchPanel' : '#newPanel';
        var trigger = document.querySelector(
            '.mode-toggle-btn[data-bs-target="' + targetId + '"]'
        );
        if (trigger && window.bootstrap && window.bootstrap.Tab) {
            window.bootstrap.Tab.getOrCreateInstance(trigger).show();
        }
        syncToggleVisual(mode);
    }

    function wireToggleClickPersistence() {
        document.querySelectorAll('.mode-toggle-btn').forEach(function (btn) {
            btn.addEventListener('shown.bs.tab', function (ev) {
                var mode = ev.target.dataset.mode;
                if (mode) {
                    safeSetStored(mode);
                    syncToggleVisual(mode);
                }
            });
        });
    }

    function determineInitialMode() {
        // Server hint takes precedence (batch_tab=true after /ce:batch POST).
        if (window.__batchTabHint === true) return 'batch';
        var stored = safeGetStored();
        if (stored === 'single' || stored === 'batch') return stored;
        return DEFAULT_MODE;
    }

    function init() {
        wireToggleClickPersistence();
        var initialMode = determineInitialMode();
        if (initialMode !== 'single') {
            // Only act when the desired mode differs from the template default
            // (#newPanel.active = single). Avoids a redundant tab activation.
            activatePane(initialMode);
        } else {
            syncToggleVisual('single');
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
