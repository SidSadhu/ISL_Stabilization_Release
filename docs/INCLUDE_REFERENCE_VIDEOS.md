# INCLUDE-50 Reference Video Gallery (Windows)

The web app now includes a searchable gallery of the 50 model classes. It can play locally available official INCLUDE dataset examples, let you mark a class as the **expected sign**, and compare that expected gloss with the latest model prediction. A match is a practice aid only; it is not a formal accuracy measurement.

## Recommended path for model validation (not the full gallery archive)

For the research baseline, use the exact official 77-video validation list rather than downloading the full Zenodo archive. This keeps the held-out test set untouched and fetches only missing validation clips. From PowerShell at the project root:

```powershell
.\.venv\Scripts\python.exe scripts\prepare_official_validation_manifest.py --repo C:\AIAssets\INCLUDE
.\.venv\Scripts\python.exe scripts\download_include_reference_clips.py --profile validation-all --ai4bharat-repo C:\AIAssets\INCLUDE --destination C:\AIAssets\INCLUDE_videos --download
```

The download prompt requires typing `FETCH CLIPS`. The resulting manifest must contain exactly 77 source-verified validation rows. Run the evaluator and report a complete official validation result only when it prints `evaluation_complete_for_official_split: true`. Do not download or inspect test clips while making model, preprocessing or threshold choices. See `IMPLEMENTATION_PLAN.md` for the full workflow.

The full-archive instructions below are optional and are only for populating the local reference gallery; they are not needed to run the recommended validation workflow.

## Important distinction

The Hugging Face `ai4bharat/INCLUDE` dataset page provides **metadata** (`parent_label`, `label`, `video_path`, `include_50`), while the video files are distributed separately through the official Zenodo record. This project ZIP does not redistribute the videos. The gallery works without video assets, but video playback stays unavailable until the metadata and dataset files are installed locally. See the [official dataset card](https://huggingface.co/datasets/ai4bharat/INCLUDE) and [official AI4Bharat code repository](https://github.com/AI4Bharat/INCLUDE).

## Step 1 — Fetch the small metadata index

From the project root in PowerShell, with the project's virtual environment active:

```powershell
.\.venv\Scripts\python.exe scripts\prepare_include_references.py
```

This fetches metadata from the Hugging Face dataset viewer API and writes `data\include50_video_metadata.json`. It does **not** download videos.

## Step 2 — Inspect the official video archive sizes

```powershell
.\scripts\download_include_videos.ps1
```

This is inventory-only by default. It displays the ZIP archive names and sizes reported by the official Zenodo record. Do not proceed unless you have sufficient disk space and bandwidth.

## Step 3 — Download and extract the archives (optional)

Only if you choose to download the full dataset:

```powershell
.\scripts\download_include_videos.ps1 -Destination "C:\AIAssets\INCLUDE_videos" -Download
```

The script asks you to type `DOWNLOAD`. It downloads **all ZIP archives listed by the record**, not only INCLUDE-50, so review the size inventory first. Keep the extracted directory structure intact so `video_path` values can resolve to files. If extraction produces a nested folder, set the root to the directory immediately above category folders such as `Greetings`, `Places`, and `Home`.

## Step 4 — Configure the video root

Edit `.env` and add/update:

```dotenv
ISL_REFERENCE_VIDEO_DIR=C:/AIAssets/INCLUDE_videos
ISL_REFERENCE_METADATA=data/include50_video_metadata.json
```

If the archives extracted into a nested folder, point `ISL_REFERENCE_VIDEO_DIR` at the correct nested root. Restart the app after editing `.env`:

```powershell
# Stop the running server with Ctrl+C first
.\scripts\start_app.ps1
```

Then refresh `http://127.0.0.1:8000`. Select a class and the first locally available official example will load. The gallery lists all 50 classes even when the videos are not yet installed.

## Practice workflow

1. Search for a class, such as `hello`, `thank you`, or `court`.
2. Choose it and watch the reference clip.
3. Click **Use as expected sign**.
4. Perform one isolated sign in the webcam capture area and submit it.
5. Review the expected gloss, prediction, confidence, capture quality, and match/mismatch note.

A low-confidence rejection or a mismatch can result from signing differences, camera framing, lighting, occlusion, preprocessing differences, or model limitations. Do not lower the confidence threshold just to force a match. Do not treat one successful match as validation.

## Privacy and licensing

The videos contain real signers. Use them respectfully and only for legitimate learning/evaluation. Review the current official dataset card/license and attribution requirements before sharing a derived dataset or public demo. This app serves videos only from the configured local directory and does not upload webcam frames to the dataset host.
