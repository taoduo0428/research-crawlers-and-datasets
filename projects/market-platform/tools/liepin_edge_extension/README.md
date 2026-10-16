# Liepin Public JD Bridge

1. Start the local bridge with `python tools/run_liepin_edge_extension_bridge.py --port 8765`.
2. Open `edge://extensions`, enable Developer mode, choose **Load unpacked**, and select this directory.
3. Open the extension's **Options**, paste the current token from `.crawler-state/liepin-edge-extension/bridge-token`, and save it.
4. Keep the normal Edge profile open. The extension creates three background tabs.

Worker IDs are `browser_forward`, `api_reverse`, and `camoufox_reverse_2`. Every `GET /next` and `POST /result` request carries the runtime token in `X-Bridge-Token`. Stop the bridge to pause new task claims.

The extension only reads public detail-page DOM and sends task results to the local bridge. It does not read, export, or log cookies or account credentials. Verification pages are reported as `security_transit` and are not bypassed.
