function analyticsApp() {
    return {
        query: "",
        currency: "USD",
        sort: "newest",
        loading: false,
        error: null,
        stats: null,
        listings: [],
        segments: null,
        chartInstance: null,

        get segmentCards() {
            if (!this.segments) {
                return [];
            }
            return [
                { key: "new_private", label: "New / Private", ...this.segments.new_private },
                { key: "new_shop", label: "New / Shop", ...this.segments.new_shop },
                { key: "used_private", label: "Used / Private", ...this.segments.used_private },
                { key: "used_shop", label: "Used / Shop", ...this.segments.used_shop },
            ];
        },

        async init() {
            if (window.Telegram && window.Telegram.WebApp) {
                Telegram.WebApp.expand();
                document.documentElement.setAttribute("data-theme", Telegram.WebApp.colorScheme);
            }
        },

        async search() {
            if (!this.query.trim()) {
                return;
            }
            this.loading = true;
            this.error = null;
            try {
                const [stats, listings, segments] = await Promise.all([
                    this.fetchJson(`/api/v1/price-stats?query=${encodeURIComponent(this.query)}&currency=${this.currency}`),
                    this.fetchJson(`/api/v1/listings?query=${encodeURIComponent(this.query)}&sort=${this.sort}&currency=${this.currency}`),
                    this.fetchJson(`/api/v1/segments?query=${encodeURIComponent(this.query)}&currency=${this.currency}`),
                ]);
                this.stats = stats;
                this.listings = listings.listings || [];
                this.segments = segments;
                this.renderChart(this.stats);
            } catch (error) {
                this.error = error.message || "Unable to load analytics";
            } finally {
                this.loading = false;
            }
        },

        async loadListings(sortOrder) {
            this.sort = sortOrder;
            if (!this.query.trim()) {
                return;
            }
            this.loading = true;
            try {
                const payload = await this.fetchJson(
                    `/api/v1/listings?query=${encodeURIComponent(this.query)}&sort=${sortOrder}&currency=${this.currency}`
                );
                this.listings = payload.listings || [];
            } catch (error) {
                this.error = error.message || "Unable to refresh listings";
            } finally {
                this.loading = false;
            }
        },

        async fetchJson(url) {
            const headers = {};
            if (window.Telegram && window.Telegram.WebApp && Telegram.WebApp.initData) {
                headers["X-Telegram-Init-Data"] = Telegram.WebApp.initData;
            }
            const response = await fetch(url, { headers });
            if (!response.ok) {
                throw new Error(`Request failed with status ${response.status}`);
            }
            return response.json();
        },

        renderChart(stats) {
            if (!stats || stats.count === 0) {
                return;
            }
            const canvas = document.getElementById("priceChart");
            if (!canvas) {
                return;
            }
            if (this.chartInstance) {
                this.chartInstance.destroy();
            }
            this.chartInstance = new Chart(canvas, {
                type: "bar",
                data: {
                    labels: ["Min", "Q1", "Median", "Q3", "Max"],
                    datasets: [
                        {
                            label: `Price (${this.currency})`,
                            data: [stats.min, stats.q1, stats.median, stats.q3, stats.max],
                            backgroundColor: [
                                "rgba(143, 45, 16, 0.55)",
                                "rgba(210, 96, 50, 0.55)",
                                "rgba(255, 154, 90, 0.75)",
                                "rgba(210, 96, 50, 0.55)",
                                "rgba(143, 45, 16, 0.55)",
                            ],
                            borderRadius: 14,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                    },
                },
            });
        },

        renderBoxPlot(stats) {
            this.renderChart(stats);
        },

        formatPrice(price) {
            const numeric = Number(price || 0);
            const symbols = { USD: "$", EUR: "€", BYN: "Br" };
            const symbol = symbols[this.currency] || "";
            return `${symbol}${numeric.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
        },
    };
}

document.addEventListener("alpine:init", () => {
    Alpine.data("analyticsApp", analyticsApp);
});
