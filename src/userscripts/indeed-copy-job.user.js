// ==UserScript==
// @name         Indeed Job Copy for Auto-ATS
// @namespace    auto-ats
// @version      1.0
// @description  Scrape an Indeed job posting and copy clean JSON to clipboard.
// @author       Auto-ATS
// @match        *://*.indeed.com/*
// @grant        GM_setClipboard
// @grant        unsafeWindow
// @run-at       document-end
// ==/UserScript==

(function () {
    'use strict';

    const NAMESPACE = 'autoats';
    let currentJobId = null;
    let injectedButton = null;
    let toastElement = null;

    // TB-016: Console feedback logger
    function log(msg) { console.log(`[AutoATS] ${msg}`); }
    function warn(msg) { console.warn(`[AutoATS] ${msg}`); }
    function error(msg) { console.error(`[AutoATS] ${msg}`); }

    // TB-015: Navigation monitor
    function resetState() {
        currentJobId = null;
        log('State reset for navigation.');
    }

    // TB-018: Job ID extractor
    function getJobId() {
        try {
            const url = new URL(window.location.href);
            return url.searchParams.get('jk') || url.searchParams.get('vjk') || null;
        } catch (e) {
            return null;
        }
    }

    // TB-001: DOM readiness sensor using MutationObserver
    function waitForContainer(callback, timeoutMs) {
        timeoutMs = timeoutMs || 10000;
        const start = Date.now();
        const ready = () => {
            const desc = getDescription();
            const title = getTitle();
            return title.length > 0 && desc.length > 100;
        };
        if (ready()) {
            callback(true);
            return;
        }
        const observer = new MutationObserver(() => {
            if (ready()) {
                observer.disconnect();
                callback(true);
            } else if (Date.now() - start > timeoutMs) {
                observer.disconnect();
                callback(false);
            }
        });
        observer.observe(document.body, { childList: true, subtree: true });
        setTimeout(() => {
            observer.disconnect();
            callback(ready());
        }, timeoutMs);
    }

    // TB-009: Data sanitizer
    function clean(text) {
        if (!text) return '';
        return String(text)
            .replace(/\s+/g, ' ')
            .replace(/[\n\t]+/g, ' ')
            .trim();
    }

    // TB-017: Error handler wrapper
    function safe(fn, fallback) {
        try {
            return fn();
        } catch (e) {
            warn(`Extraction failed: ${e.message}`);
            return fallback;
        }
    }

    // TB-002: Header scraper
    function getTitle() {
        return safe(() => {
            const el = document.querySelector('[data-testid="jobsearch-JobInfoHeader-title"]') ||
                       document.querySelector('h1[class*="JobInfoHeader"]') ||
                       document.querySelector('h1') ||
                       document.querySelector('h2') ||
                       document.querySelector('h5') ||
                       document.querySelector('[class*="jobTitle"]') ||
                       document.querySelector('[class*="JobInfoHeader"]');
            return clean(el && el.textContent);
        }, '');
    }

    // TB-003: Company metadata scraper
    function getCompany() {
        return safe(() => {
            const el = document.querySelector('[data-testid="company-info-metadata"]') ||
                       document.querySelector('[data-testid="jobsearch-JobInfoHeader-companyName"]') ||
                       document.querySelector('a[href*="/cmp/"]') ||
                       document.querySelector('span[class*="companyName"]') ||
                       document.querySelector('[class*="CompanyName"]');
            return clean(el && el.textContent);
        }, '');
    }

    // TB-004: Location extractor
    function getLocation() {
        return safe(() => {
            const el = document.querySelector('[data-testid="text-location"]') ||
                       document.querySelector('[data-testid="jobsearch-JobInfoHeader-location"]') ||
                       document.querySelector('span[class*="JobInfoHeader-location"]') ||
                       document.querySelector('[class*="Location"]');
            return clean(el && el.textContent);
        }, '');
    }

    // TB-005: Pay info slicer
    function getPay() {
        return safe(() => {
            const section = document.querySelector('[data-testid="job-details-section"]') ||
                            document.querySelector('[data-testid="salaryInfoAndJobType"]');
            const text = section ? section.textContent : document.body.textContent;
            const m = text.match(/\$[\d,]+(?:\.\d{2})?(?:\s*-\s*\$?[\d,]+(?:\.\d{2})?)?(?:\s*-\s*\$?[\d,]+(?:\.\d{2})?)?/);
            return m ? m[0] : 'NULL';
        }, 'NULL');
    }

    // TB-006: Job type slicer
    function getJobTypes() {
        return safe(() => {
            const buttons = Array.from(document.querySelectorAll('button[data-testid]'));
            const types = [];
            buttons.forEach(b => {
                const id = b.getAttribute('data-testid') || '';
                if (id.includes('job-type')) {
                    const txt = clean(b.textContent);
                    if (txt && !types.includes(txt)) types.push(txt);
                }
            });
            return types;
        }, []);
    }

    // TB-007: Benefits parser
    function getBenefits() {
        return safe(() => {
            const headers = Array.from(document.querySelectorAll('h2, h3, h4, div[role="heading"]'));
            for (const h of headers) {
                if (/benefits/i.test(h.textContent)) {
                    const parent = h.closest('div') || h.parentElement;
                    if (!parent) continue;
                    const items = Array.from(parent.querySelectorAll('li, div, span'));
                    const out = [];
                    items.forEach(item => {
                        const txt = clean(item.textContent);
                        if (txt && txt.length > 2 && txt.length < 100 && !out.includes(txt)) out.push(txt);
                    });
                    return out.slice(0, 10);
                }
            }
            return [];
        }, []);
    }

    // TB-008: Description scraper
    function getDescription() {
        return safe(() => {
            const el = document.querySelector('[data-testid="job-description"]') ||
                       document.querySelector('[data-testid="jobDescriptionText"]') ||
                       document.querySelector('div[id="jobDescriptionText"]') ||
                       document.querySelector('[data-testid="text-job-description"]') ||
                       document.querySelector('[data-testid="viewjob-job-content"]') ||
                       document.querySelector('.jobsearch-JobComponent-description') ||
                       document.querySelector('[class*="jobDescription"]');
            if (el) {
                const clone = el.cloneNode(true);
                const garbage = clone.querySelectorAll('script, style, svg, img, button, input, nav, header, footer');
                garbage.forEach(n => n.remove());
                const txt = clean(clone.textContent);
                if (txt.length > 100) return txt;
            }
            // Fallback: find the largest text block that isn't in header/footer/nav
            let best = '';
            const divs = document.querySelectorAll('div, section, article');
            divs.forEach(d => {
                const clone = d.cloneNode(true);
                clone.querySelectorAll('script, style, svg, img, button, input, nav, header, footer').forEach(n => n.remove());
                const txt = clean(clone.textContent);
                if (txt.length > best.length && txt.length > 500) best = txt;
            });
            return best;
        }, '');
    }

    // TB-010: Clipboard aggregator
    function aggregateJobData() {
        const data = {
            key: getJobId() || `indeed_${Date.now()}`,
            title: getTitle(),
            company: getCompany(),
            location: getLocation(),
            pay: getPay(),
            job_types: getJobTypes(),
            benefits: getBenefits(),
            description: getDescription(),
            url: window.location.href,
            source: 'indeed',
            datePublished: new Date().toISOString()
        };
        return data;
    }

    // TB-014: CSS scope protection
    function injectStyles() {
        if (document.getElementById(`${NAMESPACE}-styles`)) return;
        const style = document.createElement('style');
        style.id = `${NAMESPACE}-styles`;
        style.textContent = `
            .${NAMESPACE}-btn {
                position: fixed !important;
                top: 12px !important;
                right: 12px !important;
                z-index: 2147483647 !important;
                padding: 10px 16px !important;
                border: 2px solid #ff0000 !important;
                border-radius: 6px !important;
                background: #ff0000 !important;
                color: #ffffff !important;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif !important;
                font-size: 13px !important;
                font-weight: 700 !important;
                cursor: pointer !important;
                box-shadow: 0 2px 8px rgba(0,0,0,0.2) !important;
            }
            .${NAMESPACE}-btn:hover { background: #cc0000 !important; border-color: #cc0000 !important; }
            .${NAMESPACE}-toast {
                position: fixed !important;
                top: 56px !important;
                right: 12px !important;
                z-index: 2147483647 !important;
                padding: 10px 16px !important;
                border-radius: 6px !important;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif !important;
                font-size: 13px !important;
                font-weight: 500 !important;
                color: #fff !important;
                background: #16a34a !important;
                box-shadow: 0 2px 8px rgba(0,0,0,0.2) !important;
                opacity: 0;
                transition: opacity 0.3s ease !important;
                pointer-events: none !important;
                max-width: 300px !important;
            }
            .${NAMESPACE}-toast.show { opacity: 1 !important; }
            .${NAMESPACE}-toast.error { background: #dc2626 !important; }
        `;
        document.head.appendChild(style);
    }

    // TB-019: UI feedback overlay
    function showToast(msg, isError) {
        if (!toastElement) {
            toastElement = document.createElement('div');
            toastElement.className = `${NAMESPACE}-toast`;
            document.body.appendChild(toastElement);
        }
        toastElement.textContent = msg;
        toastElement.classList.toggle('error', !!isError);
        toastElement.classList.add('show');
        setTimeout(() => toastElement.classList.remove('show'), 2500);
    }

    // TB-011 / TB-012: UI injection + event listener
    function ensureButton() {
        if (injectedButton && document.getElementById(injectedButton.id)) return;
        injectStyles();
        injectedButton = document.createElement('button');
        injectedButton.id = `${NAMESPACE}-copy-btn`;
        injectedButton.className = `${NAMESPACE}-btn`;
        injectedButton.textContent = 'Copy Job';
        injectedButton.addEventListener('click', () => {
            const jk = getJobId();
            if (!jk) {
                showToast('No job key in URL.', true);
                return;
            }
            injectedButton.textContent = 'Copying...';
            waitForContainer((ready) => {
                const data = aggregateJobData();
                log(`Extract attempt: title="${data.title}" desc_len=${data.description.length}`);
                if (!data.title || !data.description) {
                    if (!ready) warn('Job container may still be loading.');
                    showToast('Could not extract job data. Wait for page to load.', true);
                    injectedButton.textContent = 'Copy Job';
                    return;
                }
                const json = JSON.stringify(data, null, 2);
                try {
                    GM_setClipboard(json, 'json');
                    showToast(`Copied: ${data.title} @ ${data.company}`);
                    log('Job copied to clipboard.');
                } catch (e) {
                    showToast('Clipboard failed. Check Tampermonkey grants.', true);
                    error(e.message);
                }
                injectedButton.textContent = 'Copy Job';
            });
        });
        document.body.appendChild(injectedButton);
        log('Copy Job button injected.');
    }

    // Init + navigation handling
    function init() {
        const jk = getJobId();
        if (!jk) {
            // Remove button if navigated away from a job page
            if (injectedButton) {
                injectedButton.remove();
                injectedButton = null;
            }
            return;
        }
        resetState();
        currentJobId = jk;
        ensureButton();
        log(`Initialized on job ${jk}.`);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Re-init on URL changes (Indeed SPA)
    let lastUrl = location.href;
    setInterval(() => {
        if (location.href !== lastUrl) {
            lastUrl = location.href;
            init();
        }
    }, 1000);

    // Also re-check if the button disappears due to Indeed re-rendering the page
    setInterval(() => {
        if (getJobId() && !document.getElementById(`${NAMESPACE}-copy-btn`)) {
            ensureButton();
        }
    }, 2000);
})();
