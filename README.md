# auto-ATS

Indeed job saver scripts for the Auto-ATS (Automated Application Tracking System).

## Overview

This repository contains Tampermonkey userscripts for saving job postings from Indeed to the auto-ATS system.

## Files

### `src/userscripts/indeed-saver.js`
- Main script for saving Indeed job postings
- Works with Indeed's modern two-pane layout
- Sends job data to `http://localhost:5000/api/tm-save`

### `src/userscripts/indeed-job-saver.js`
- Alternative Indeed job saver script
- Similar functionality with slightly different implementation

## Installation

1. Install [Tampermonkey](https://www.tampermonkey.net/) browser extension
2. Copy the contents of either `indeed-saver.js` or `indeed-job-saver.js` 
3. Create a new userscript in Tampermonkey and paste the code
4. Save and enable the script
5. Visit any Indeed job posting page
6. Click the "Save to ATS" button that appears in the top-right corner

## Configuration

The scripts are configured to send data to `http://localhost:5000/api/tm-save`. 
Make sure your auto-ATS backend is running and accessible at this endpoint.

## Documentation

See `docs/App_Indeed_URL_Integration_via_Apify.md` for detailed information about Indeed URL integration via Apify.

## Related Projects

- `trevino_war_room-master/` - Contains the main auto-ATS backend system (Flask server)
- The scripts are designed to work with the Trevino War Room v6.0 system

## Usage

When viewing an Indeed job posting:
1. Click the "Save to ATS" button that appears
2. The script will wait for the job description to load
3. Capture the job HTML and send it to your local auto-ATS instance
4. Receive success/error notifications via toast messages

## Requirements

- Browser with Tampermonkey extension
- Running auto-ATS backend (typically on localhost:5000)
- Indeed job posting page open
