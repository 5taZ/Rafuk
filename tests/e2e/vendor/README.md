# tests/e2e/vendor/

Place `axe.min.js` here for offline axe-core injection. The smoke test
fails without it (OPS-12).

Download the release the `e2e-smoke` job in `.github/workflows/ci.yml`
pins, for example:

    curl -fsSL https://cdn.jsdelivr.net/npm/axe-core@4.14.0/axe.min.js -o tests/e2e/vendor/axe.min.js
