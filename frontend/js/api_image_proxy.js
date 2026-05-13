function createImageProxyLoader(core) {
    const proxyImageObjectUrls = new Map();

    function isImageProxyUrl(url) {
        return typeof url === "string" &&
            url.startsWith("/api/v1/img/") &&
            !url.startsWith("//");
    }

    async function fetchProxyImageObjectUrl(url) {
        if (!isImageProxyUrl(url)) throw new Error("Invalid image proxy URL");
        const cached = proxyImageObjectUrls.get(url);
        if (cached?.objectUrl) return cached.objectUrl;
        if (cached?.promise) return cached.promise;
        const entry = { objectUrl: "", promise: null };
        entry.promise = fetch(url, {
            headers: {
                ...core.telegramHeaders(),
                Accept: "image/avif,image/webp,image/*,*/*",
            },
        }).then(async (response) => {
            if (!response.ok) throw new Error("Image proxy request failed");
            const objectUrl = URL.createObjectURL(await response.blob());
            if (proxyImageObjectUrls.get(url) !== entry) {
                URL.revokeObjectURL(objectUrl);
                throw new Error("Image proxy request superseded");
            }
            entry.objectUrl = objectUrl;
            entry.promise = null;
            return objectUrl;
        }).catch((err) => {
            if (proxyImageObjectUrls.get(url) === entry) proxyImageObjectUrls.delete(url);
            throw err;
        });
        proxyImageObjectUrls.set(url, entry);
        return entry.promise;
    }

    function clearProxyImageObjectUrls() {
        for (const entry of proxyImageObjectUrls.values()) {
            if (entry.objectUrl) URL.revokeObjectURL(entry.objectUrl);
        }
        proxyImageObjectUrls.clear();
    }

    return {
        fetchProxyImageObjectUrl,
        clearProxyImageObjectUrls,
    };
}
