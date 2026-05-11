/**
 * Central frontend configuration — single source of truth for timeouts,
 * thresholds, animation durations, and UI limits.
 *
 * Attached to window.APP_CONFIG so every legacy script can read it
 * without ES-module infrastructure.
 */
(function () {
  "use strict";

  window.APP_CONFIG = Object.freeze({
    // Timeouts, delays, intervals (ms)
    TIMEOUTS: Object.freeze({
      FETCH_DEFAULT: 90_000,
      FETCH_EXPORT: 120_000,
      IN_FLIGHT_GUARD: 30_000,
      TRACKER_REFRESH: 30_000,
      AI_POLL_INTERVAL: 3_000,
      AI_POLL_REQUEST: 12_000,
      URL_REVOCATION: 60_000,
      DEFERRED_FOCUS: 320,
      DOM_ACTION_DELAY: 180,
      TRANSITION_CLEANUP: 240,
      SEARCH_DEBOUNCE: 1_200,
      DOUBLE_TAP_WINDOW: 300,
      CAROUSEL_EXIT_DELAY: 140,
      RELOAD_AFTER_ACTION: 1_500,
    }),

    // Toast durations (ms)
    TOAST: Object.freeze({
      DURATION_DEFAULT: 3_000,
      DURATION_LOADING: 1_400,
      DURATION_COPY: 1_400,
      MAX_VISIBLE: 3,
    }),

    // Animation & transition durations (ms)
    ANIMATION: Object.freeze({
      TRANSITION_DEFAULT: 220,
      TRANSITION_COMMIT: 200,
      TRANSITION_CLEANUP: 240,
      TOAST_ENTER: 200,
      TOAST_EXIT: 200,
      REDUCED_MOTION: 10,
    }),

    // Swipe, touch, and scroll thresholds
    GESTURE: Object.freeze({
      SWIPE_THRESHOLD_CAROUSEL: 50,
      SWIPE_THRESHOLD_WATCHLIST: 80,
      SWIPE_SLOP: 10,
      PULL_TO_REFRESH_COMMIT: 70,
      PULL_TO_REFRESH_MAX: 140,
      PINCH_CLAMP_MARGIN: 40,
      DOUBLE_TAP_TOLERANCE: 30,
    }),

    // Pagination, buffer, and list limits
    LIST: Object.freeze({
      PAGE_SIZE: 50,
      VIRTUAL_THRESHOLD_WATCHLIST: 30,
      VIRTUAL_THRESHOLD_TRACKERS: 50,
      VIRTUAL_ITEM_HEIGHT_WATCHLIST: 220,
      VIRTUAL_ITEM_HEIGHT_DEFAULT: 160,
      VIRTUAL_BUFFER: 5,
      VIRTUAL_MAX_HEIGHT: "70vh",
    }),

    // Retry, poll, and guard limits
    POLLING: Object.freeze({
      AI_MAX_ATTEMPTS: 120,
      AI_MAX_ERRORS: 4,
      AI_MIN_POLLS: 5,
    }),

    // Progress animation constants (ms, percentages)
    PROGRESS: Object.freeze({
      AI_EXPECTED_TIME: 26_000,
      ASSISTANT_EXPECTED_TIME: 18_000,
      SOFT_CAP_PERCENT: 94,
      START_PERCENT: 3,
    }),

    // UI capacity limits
    UI_LIMITS: Object.freeze({
      MAX_RECENT_SEARCHES: 10,
      MAX_ASSISTANT_HISTORY: 20,
      MAX_SIMILAR_LISTINGS: 8,
      MAX_PARAM_CHIPS: 10,
    }),

    // Image & media limits
    MEDIA: Object.freeze({
      MAX_PHOTOS: 4,
      MAX_IMAGE_DIMENSION: 1_280,
      JPEG_QUALITY: 0.78,
      MAX_DESCRIPTION_HISTORY: 500,
      MAX_MARKET_SUMMARY_HISTORY: 300,
    }),

    // Cache & analytics
    CACHE: Object.freeze({
      LISTINGS_TTL: 2 * 60 * 1_000,
      DEFAULT_ANALYTICS_DAYS: 90,
    }),

    // Content truncation
    TRUNCATION: Object.freeze({
      MAX_DESCRIPTION_HISTORY: 500,
      MAX_MARKET_SUMMARY_HISTORY: 300,
    }),
  });
})();
