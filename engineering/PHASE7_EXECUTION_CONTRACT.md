# Phase 7 execution contract

## Immutable inputs

- Official validation manifest: exact pinned INCLUDE-50 validation list, 77 rows.
- AI4Bharat repository HEAD: `df348f4a640060d4d39f02556d9f96851e8c46c7`.
- Six diagnostic clips: Good Morning, White, Big/Large, Short, Car, and I.
- Source presentation timestamps: FFprobe `best_effort_timestamp_time`.

## Controlled variables

Same browser, local source file, playback behavior, six clips, Canvas width (640), JPEG quality (0.92), frame cap (169), capture duration cap (6 seconds), timestamp field (`metadata.mediaTime`), and synchronous `drawImage` call in the video-frame callback.

## Experimental variable

Mode A uses a 39 ms callback acceptance gate and synchronous `toDataURL`. Mode B accepts every unique callback and initiates asynchronous `toBlob` encoding without waiting for completion before scheduling the next callback.

## No-go conditions

The runner stops before capture if the Python environment is not 3.12, the official manifest is invalid, the pinned repo/label map cannot be verified, FFprobe is unavailable, IoT is enabled, IoT mappings are non-empty, or the application's pre-IoT lockdown is absent. It also stops if either mode does not produce all six validated capture files or if any source PTS extraction fails.

## Analysis validity

The `.islbrw` binary protocol parser checks each JPEG's size and boundary, frame order, monotonic `mediaTime`, source path/label identity, expected capture dimensions, JPEG signatures, capture duration, and header. The analysis separately verifies the capture-index SHA-256 and source file SHA-256, then aligns each captured `mediaTime` to the nearest FFprobe decoded-frame PTS.

## Non-claims

Phase 7 does not measure model accuracy, hand detection, feature-slot stability, recognition latency, production webcam behavior, or user-perceived live performance. It measures browser frame-callback capture completeness and JPEG encoding behavior on six controlled validation videos.
