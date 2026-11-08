# Phase 7 mode-string template fix

## Root cause

The server replaces `__PHASE7_MODE__` with a JSON string (for example, `"current_40ms"`). The HTML template previously surrounded the placeholder with single quotes:

```js
const MODE = '__PHASE7_MODE__';
```

That rendered as a string containing quote characters (`'"current_40ms"'`), so neither supported mode branch matched. The page threw `Unsupported Phase 7 mode` before browser-support and manifest initialization completed. This exactly explains the observed page state: `Loading...`, `Checking browser support...`, and `Waiting for manifest`.

## Fix

The template now treats the replacement as a JavaScript expression:

```js
const MODE = __PHASE7_MODE__;
```

The existing server's `json.dumps(mode)` replacement now renders valid JavaScript:

```js
const MODE = "current_40ms";
```

or

```js
const MODE = "every_presented_frame";
```

## Installation

Overlay this ZIP into the existing project root. The running Phase 7 server reads this HTML template for each `GET /`, so it does not need to be restarted. Hard-refresh the currently open page (`Ctrl+F5`), select the video folder again, verify six target paths match, then run Mode A.

The official 77-row manifest is correct. The browser page verifies all 77 rows and filters six target diagnostic clips; do not change the manifest or server `expected_count`.

This patch changes only the Phase 7 diagnostic HTML template and adds a focused regression test. It does not touch production recognition code, the model/checkpoint, captures already saved, configuration, or IoT settings.
