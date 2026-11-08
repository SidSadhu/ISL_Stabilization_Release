# Reference gallery multi-clip update

The backend now includes each metadata row's `split` value in `/api/reference-videos` and sorts each class's videos in train/val/test order. The browser UI adds a clip selector, displays the selected split, and lets the user switch among all local videos available for the chosen class. No recognition model, input tensor, capture threshold, or IoT policy is changed.

The selective downloader is described in `docs/SELECTIVE_REFERENCE_CLIPS.md`.
