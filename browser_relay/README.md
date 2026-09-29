# BirdEye Browser Relay

Local browser transport for BirdEye loops that does not use Chrome remote debugging or CDP.

## Architecture

ChatGPT tab
-> Chrome extension
-> localhost HTTP relay
-> BirdEye loop

BirdEye remains responsible for loop state and local execution. Existing BirdEye workspace indexing and SHA-256 records remain authoritative for files.

## Install

From the BirdEye repository:

    powershell -ExecutionPolicy Bypass -File .\browser_relay\install_browser_relay.ps1

The relay listens on:

    http://127.0.0.1:7726

Then open chrome://extensions, enable Developer mode, choose Load unpacked, and select:

    browser_relay\extension

No Chrome remote-debugging port is enabled.

## API

GET  /health
GET  /tabs
POST /tabs/register
POST /tabs/unregister
POST /commands/next
POST /commands/complete
POST /read
POST /reply

Commands are bound to a specific registered Chrome tab ID.

## Boundary

This is the browser transport layer, not the complete autonomous command loop. The loop controller can use it to read a specific ChatGPT tab and post a result back to that same tab.

DOM selectors are deliberately explicit. If the ChatGPT UI changes, the relay reports an error rather than claiming success.
