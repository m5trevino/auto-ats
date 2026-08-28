// ==UserScript==
// @name         Indeed Job Saver for Auto-ATS
// @namespace    auto-ats
// @version      1.1
// @description  Save an Indeed job posting to auto-ATS with one click.
// @author       Auto-ATS
// @match        *://*.indeed.com/*
// @grant        GM_xmlhttpRequest
// @connect      apis.indeed.com
// @connect      localhost
// @run-at       document-end
// ==/UserScript==

(function () {
    'use strict';

    // ------------------------------------------------------------------
    // CONFIG
    // ------------------------------------------------------------------
    const API_URL = "http://localhost:5000/api/tm-save";

    // ------------------------------------------------------------------
    // PAGE VALIDATION — only fire on actual job detail pages
    // ------------------------------------------------------------------
    function getJobDetailRoot() {
        return document.getElementById('vjs-container') ||
               document.getElementById('job-full-details') ||
               document.body;
    }

    function isJobDetailPage() {
        const root = getJobDetailRoot();
        if (!root) return false;
        // Modern two-pane detail pane
        if (root.querySelector('[data-testid="jobsearch-JobInfoHeader-title"]') ||
            root.querySelector('.jobsearch-JobInfoHeader-title') ||
            root.querySelector('.jobsearch-JobComponent-description')) {
            return true;
        }
        // Traditional job detail page
        if (document.querySelector('div[id="jobDescriptionText"]')) return true;
        return false;
    }

    // ------------------------------------------------------------------
    // UTILS
    // ------------------------------------------------------------------
    function getJk() {
        const params = new URLSearchParams(window.location.search);
        return params.get("jk") || params.get("vjk");
    }

    // Wait for Indeed's dynamic detail pane to finish loading the description.
    function waitForDescription(callback, timeoutMs) {
        timeoutMs = timeoutMs || 4000;
        const root = getJobDetailRoot();
        const start = Date.now();
        const timer = setInterval(() => {
            const descEl = root.querySelector('.jobsearch-JobComponent-description') ||
                           root.querySelector('div[id="jobDescriptionText"]') ||
                           root.querySelector('[data-testid="text-job-description"]');
            const text = descEl ? descEl.textContent.trim() : "";
            if (text.length > 200) {
                clearInterval(timer);
                callback(true);
                return;
            }
            if (Date.now() - start > timeoutMs) {
                clearInterval(timer);
                callback(false);
            }
        }, 250);
    }

    function showToast(msg, type = "info") {
        const existing = document.getElementById("ats-toast");
        if (existing) existing.remove();

        const toast = document.createElement("div");
        toast.id = "ats-toast";
        toast.textContent = msg;
        toast.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 999999;
            padding: 12px 20px;
            border-radius: 8px;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            font-size: 14px;
            font-weight: 500;
            color: #fff;
            background: ${type === "error" ? "#dc2626" : type === "success" ? "#16a34a" : "#2563eb"};
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            transition: opacity 0.3s ease;
        `;
        document.body.appendChild(toast);
        setTimeout(() => { toast.style.opacity = "0"; setTimeout(() => toast.remove(), 300); }, 3000);
    }

    // ------------------------------------------------------------------
    // CAPTURE RAW JOB HTML — let the AI engine extract structured data
    // ------------------------------------------------------------------
    function captureJobHTML(jk) {
        const root = getJobDetailRoot();
        if (!isJobDetailPage()) {
            throw new Error("No job detail pane found. Click a job title first.");
        }

        const jobUrl = window.location.href;
        const html = root.outerHTML || document.documentElement.outerHTML;

        if (html.length < 500) {
            throw new Error("Job detail pane is too small. Wait for the page to load.");
        }

        return {
            key: jk,
            url: jobUrl,
            html: html,
            source: "indeed"
        };
    }

    // ------------------------------------------------------------------
    // SEND TO LOCAL AUTO-ATS
    // ------------------------------------------------------------------
    function sendToATS(payload, onDone, onErr) {
        GM_xmlhttpRequest({
            method: "POST",
            url: API_URL,
            headers: { "Content-Type": "application/json" },
            data: JSON.stringify([payload]),
            timeout: 60000,
            onload: function (resp) {
                try {
                    const data = JSON.parse(resp.responseText);
                    if (resp.status >= 200 && resp.status < 300 && data.status === "saved") {
                        onDone(data);
                    } else {
                        onErr(data.error || "ATS returned error");
                    }
                } catch (e) {
                    onErr("ATS parse error: " + e.message);
                }
            },
            onerror: function () {
                onErr("Network error sending to auto-ATS. Is the server running on localhost:5000?");
            },
            ontimeout: function () {
                onErr("Timeout sending to auto-ATS. AI extraction may be slow.");
            }
        });
    }

    // ------------------------------------------------------------------
    // BUTTON (always adds to body — no nav dependency)
    // ------------------------------------------------------------------
    function addButton() {
        if (document.getElementById("ats-save-btn")) return true;

        console.log("[ATS] Adding button to page");

        const btn = document.createElement("button");
        btn.id = "ats-save-btn";
        btn.textContent = "Save to ATS";
        btn.style.cssText = `
            position: fixed !important;
            top: 10px !important;
            right: 10px !important;
            z-index: 2147483647 !important;
            padding: 8px 16px !important;
            border: 2px solid #ff0000 !important;
            border-radius: 4px !important;
            background: #ff0000 !important;
            color: #ffffff !important;
            font-family: sans-serif !important;
            font-size: 13px !important;
            font-weight: bold !important;
            cursor: pointer !important;
        `;

        btn.onclick = function () {
            const jk = getJk();
            console.log("[ATS] Clicked. jk=", jk);
            if (!jk) {
                showToast("No job key in URL", "error");
                return;
            }
            btn.textContent = "Loading...";
            btn.disabled = true;

            waitForDescription(function (ready) {
                try {
                    const payload = captureJobHTML(jk);
                    if (!ready) console.log("[ATS] Warning: description may still be loading");
                    btn.textContent = "Saving...";
                    sendToATS(
                        payload,
                        function (data) {
                            console.log("[ATS] Saved OK");
                            btn.textContent = "Saved";
                            btn.style.background = "#00aa00";
                            showToast("Saved job to ATS", "success");
                            setTimeout(function () {
                                btn.textContent = "Save to ATS";
                                btn.style.background = "#ff0000";
                                btn.disabled = false;
                            }, 2000);
                        },
                        function (err) {
                            console.log("[ATS] Save failed:", err);
                            btn.textContent = "Save to ATS";
                            btn.disabled = false;
                            showToast(err, "error");
                        }
                    );
                } catch (e) {
                    console.log("[ATS] Capture error:", e.message);
                    btn.textContent = "Save to ATS";
                    btn.disabled = false;
                    showToast(e.message, "error");
                }
            });
        };

        document.body.appendChild(btn);
        console.log("[ATS] Button added to body");
        return true;
    }

    // ------------------------------------------------------------------
    // INIT
    // ------------------------------------------------------------------
    function init() {
        // Always add the button on Indeed pages; the click handler will
        // validate that a job detail pane is actually open.
        const jk = getJk();
        console.log("[ATS] init() running. jk=", jk, "href=", window.location.href);
        addButton();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
