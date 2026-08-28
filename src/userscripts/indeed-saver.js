// ==UserScript==
// @name         Indeed Saver for Auto-ATS
// @namespace    auto-ats
// @version      3.0
// @description  Scrape job data from Indeed's modern two-pane layout and send to auto-ATS
// @author       Auto-ATS
// @match        *://*.indeed.com/*
// @grant        GM_xmlhttpRequest
// @connect      localhost
// @run-at       document-end
// ==/UserScript==

(function () {
    'use strict';

    function getJk() {
        var p = new URLSearchParams(window.location.search);
        return p.get("jk") || p.get("vjk");
    }

    // ------------------------------------------------------------------
    // Return the DOM root that contains the actual job details.
    // Modern Indeed uses a two-pane homepage: the detail pane is
    // #vjs-container / #job-full-details. Traditional job pages use
    // #jobDescriptionText.
    // ------------------------------------------------------------------
    function getJobDetailRoot() {
        var pane = document.getElementById('vjs-container') ||
                   document.getElementById('job-full-details');
        if (pane) return pane;
        return document.body;
    }

    function isJobDetailPage() {
        var root = getJobDetailRoot();
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

    function showToast(msg, type) {
        var existing = document.getElementById("ats-toast");
        if (existing) existing.remove();
        var toast = document.createElement("div");
        toast.id = "ats-toast";
        toast.textContent = msg;
        var bg = type === "error" ? "#dc2626" : type === "success" ? "#16a34a" : "#2563eb";
        toast.style.cssText = "position:fixed;top:20px;right:20px;z-index:2147483647;padding:12px 20px;border-radius:8px;font-family:sans-serif;font-size:14px;font-weight:500;color:#fff;background:" + bg + ";box-shadow:0 4px 12px rgba(0,0,0,0.15);";
        document.body.appendChild(toast);
        setTimeout(function () { toast.style.opacity = "0"; setTimeout(function () { toast.remove(); }, 300); }, 3000);
    }

    function getText(el) {
        return el ? el.textContent.trim() : "";
    }

    // Wait for Indeed's dynamic detail pane to finish loading the description.
    function waitForDescription(callback, timeoutMs) {
        timeoutMs = timeoutMs || 4000;
        var root = getJobDetailRoot();
        var start = Date.now();
        var timer = setInterval(function () {
            var descEl = root.querySelector('.jobsearch-JobComponent-description') ||
                         root.querySelector('div[id="jobDescriptionText"]') ||
                         root.querySelector('[data-testid="text-job-description"]');
            var text = descEl ? descEl.textContent.trim() : "";
            if (text.length > 200) {
                clearInterval(timer);
                callback(true);
                return;
            }
            if (Date.now() - start > timeoutMs) {
                clearInterval(timer);
                callback(false); // timed out, but we'll capture what we have
            }
        }, 250);
    }

    function captureJobHTML(jk) {
        var root = getJobDetailRoot();
        if (!isJobDetailPage()) {
            throw new Error("No job detail pane found. Click a job title first.");
        }

        var jobUrl = window.location.href;
        var html = root.outerHTML || document.documentElement.outerHTML;

        // Basic sanity check: ensure we captured a real job description.
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

    function sendToATS(payload, onDone, onErr) {
        GM_xmlhttpRequest({
            method: "POST",
            url: "http://localhost:5000/api/tm-save",
            headers: { "Content-Type": "application/json" },
            data: JSON.stringify([payload]),
            timeout: 60000,
            onload: function (resp) {
                try {
                    var data = JSON.parse(resp.responseText);
                    if (resp.status >= 200 && resp.status < 300 && data.status === "saved") {
                        onDone(data);
                    } else {
                        onErr(data.error || "ATS error");
                    }
                } catch (e) {
                    onErr("Parse error");
                }
            },
            onerror: function () {
                onErr("Cannot reach auto-ATS on localhost:5000");
            },
            ontimeout: function () {
                onErr("Request to auto-ATS timed out (AI extraction may be slow).");
            }
        });
    }

    function addButton() {
        if (document.getElementById("ats-save-btn")) return true;
        if (!document.body) {
            console.log("[ATS] document.body not ready yet");
            return false;
        }
        var btn = document.createElement("button");
        btn.id = "ats-save-btn";
        btn.textContent = "Save to ATS";
        btn.style.cssText = "position:fixed;top:10px;right:10px;z-index:2147483647;padding:8px 16px;border:2px solid #ff0000;border-radius:4px;background:#ff0000;color:#fff;font-family:sans-serif;font-size:13px;font-weight:bold;cursor:pointer;";
        btn.onclick = function () {
            var jk = getJk();
            if (!jk) { showToast("No job key in URL", "error"); return; }
            btn.textContent = "Loading...";
            btn.disabled = true;

            waitForDescription(function (ready) {
                try {
                    var payload = captureJobHTML(jk);
                } catch (e) {
                    showToast(e.message, "error");
                    btn.textContent = "Save to ATS";
                    btn.disabled = false;
                    return;
                }
                if (!ready) {
                    console.log("[ATS] Warning: description may still be loading");
                }
                btn.textContent = "Saving...";
                sendToATS(payload, function (data) {
                    btn.textContent = "Saved";
                    btn.style.background = "#00aa00";
                    btn.style.borderColor = "#00aa00";
                    showToast("Saved job to ATS", "success");
                    setTimeout(function () {
                        btn.textContent = "Save to ATS";
                        btn.style.background = "#ff0000";
                        btn.style.borderColor = "#ff0000";
                        btn.disabled = false;
                    }, 2000);
                }, function (err) {
                    btn.textContent = "Save to ATS";
                    btn.disabled = false;
                    showToast(err, "error");
                });
            });
        };
        document.body.appendChild(btn);
        console.log("[ATS] Button added to page");
        return true;
    }

    function init() {
        // Always add the button on Indeed pages; the click handler will
        // validate that a job detail pane is actually open.
        console.log("[ATS] init() running on " + window.location.href);
        var attempts = 0;
        var timer = setInterval(function () {
            attempts++;
            if (addButton()) {
                clearInterval(timer);
                return;
            }
            if (attempts > 50) {
                clearInterval(timer);
                console.log("[ATS] Gave up after 50 attempts");
            }
        }, 100);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
