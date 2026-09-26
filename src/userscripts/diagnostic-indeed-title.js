// Run this in the browser console (F12 → Console) on the Indeed job page.
// It will print the actual selectors and heading elements used by Indeed,
// so we can fix the Tampermonkey script's title extraction.

(() => {
    const titleSelectors = [
        '[data-testid="jobsearch-JobInfoHeader-title"]',
        'h1',
        'h2',
        '[class*="JobInfoHeader-title"]',
        '[class*="jobTitle"]',
        '[class*="jobTitle"]',
        '[class*="title"]'
    ];
    const results = {};
    titleSelectors.forEach(sel => {
        const el = document.querySelector(sel);
        results[sel] = el ? el.textContent.trim().slice(0, 120) : null;
    });

    // Also dump the first few heading-like elements
    const headings = Array.from(document.querySelectorAll('h1, h2, [role="heading"], [class*="title"], [class*="heading"]')).slice(0, 10).map(el => ({
        tag: el.tagName,
        class: el.className.slice(0, 80),
        text: el.textContent.trim().slice(0, 120)
    }));

    console.log(JSON.stringify({ selectors: results, headings }, null, 2));
})();
