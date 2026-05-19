function createImageProxyLoader(core) {
    const MAX_CACHED_IMAGES = 50;
    const proxyImageObjectUrls = new Map();

    function revokeOldest() {
        var firstKey = proxyImageObjectUrls.keys().next().value;
        if (firstKey === undefined) return;
        var entry = proxyImageObjectUrls.get(firstKey);
        if (entry.objectUrl) URL.revokeObjectURL(entry.objectUrl);
        proxyImageObjectUrls.delete(firstKey);
    }

    function isImageProxyUrl(url) {
        return typeof url === "string" &&
            url.startsWith("/api/v1/img/") &&
            !url.startsWith("//");
    }

    async function fetchProxyImageObjectUrl(url) {
        if (!isImageProxyUrl(url)) throw new Error("Invalid image proxy URL");
        var cached = proxyImageObjectUrls.get(url);
        if (cached?.objectUrl) return cached.objectUrl;
        if (cached?.promise) return cached.promise;
        var entry = { objectUrl: "", promise: null };
        entry.promise = fetch(url, {
            headers: {
                ...core.telegramHeaders(),
                Accept: "image/avif,image/webp,image/*,*/*",
            },
        }).then(async function (response) {
            if (!response.ok) throw new Error("Image proxy request failed");
            var objectUrl = URL.createObjectURL(await response.blob());
            if (proxyImageObjectUrls.get(url) !== entry) {
                URL.revokeObjectURL(objectUrl);
                throw new Error("Image proxy request superseded");
            }
            entry.objectUrl = objectUrl;
            entry.promise = null;
            return objectUrl;
        }).catch(function (err) {
            if (proxyImageObjectUrls.get(url) === entry) proxyImageObjectUrls.delete(url);
            throw err;
        });
        proxyImageObjectUrls.set(url, entry);
        // C1: LRU eviction — revoke oldest blob when cache exceeds limit
        while (proxyImageObjectUrls.size > MAX_CACHED_IMAGES) {
            revokeOldest();
        }
        return entry.promise;
    }

    function clearProxyImageObjectUrls() {
        for (var entry of proxyImageObjectUrls.values()) {
            if (entry.objectUrl) URL.revokeObjectURL(entry.objectUrl);
        }
        proxyImageObjectUrls.clear();
    }

    return {
        fetchProxyImageObjectUrl,
        clearProxyImageObjectUrls,
    };
}
