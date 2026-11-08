# INCLUDE-50 reference clips and official validation workflow

> This document supersedes the older mapped-review instructions. Do not use the historical five-sign mapping or download test clips for model selection. The IoT labels are currently unselected and the default IoT mapping is empty.

## Reference and validation policy

Use official training examples only for learning the signs and broadening the local reference gallery. Use the **exact 77-item validation split** for model evaluation and threshold analysis. The held-out test list must remain unused until the model, feature extractor, capture settings and confidence threshold are frozen.

## Prepare metadata (optional gallery index)

From the project root in PowerShell:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location).Path "src")
.\.venv\Scripts\python.exe .\scripts\prepare_include_references.py
```

This retrieves the small metadata index, not video bytes. Keep downloaded clips in `C:\AIAssets\INCLUDE_videos`, outside the source release.

## Obtain the exact official validation split

Run the controlled workflow from the project root:

```powershell
.\scripts\run_official_validation_workflow.ps1
```

It verifies the pinned AI4Bharat commit and the SHA256 of `train_test_paths/include50_val.txt`, creates the 77-entry manifest, previews missing validation clips, and asks for the exact confirmation `FETCH CLIPS` before downloading only the missing official validation examples. It never selects test samples.

After the workflow, inspect `outputs\include50_validation_baseline\metrics.json`. A complete official validation result must have `evaluation_complete_for_official_split: true`, 77 attempted and successful examples, zero failures, and a verified manifest.

## Download additional train references only

The optional `coverage` profile selects one training reference for a class with no local clip. Preview first, review the class and path list, and only then use `--download` with the typed confirmation. Do not use validation or test examples as training data.

## Respect dataset subjects and provenance

The raw video files are not part of the application package. Keep the dataset license/attribution with any research presentation, avoid casual redistribution of source videos, and retain the original split provenance in experiment reports.
