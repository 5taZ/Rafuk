var LazyAI = (function () {
    var _cache = {};

    function _loadScript(src) {
        if (_cache[src]) return _cache[src];
        var promise = new Promise(function (resolve, reject) {
            var el = document.createElement("script");
            el.src = src;
            el.onload = resolve;
            el.onerror = function () {
                delete _cache[src];
                reject(new Error("Failed to load " + src));
            };
            document.head.appendChild(el);
        });
        _cache[src] = promise;
        return promise;
    }

    function loadApiAi() {
        return _loadScript("js/api_ai.js?v=20260509");
    }

    function loadListingAssistant() {
        return _loadScript("js/api_listing_assistant.js?v=20260509");
    }

    return { loadApiAi: loadApiAi, loadListingAssistant: loadListingAssistant };
})();
