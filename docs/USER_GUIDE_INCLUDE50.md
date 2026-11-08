# User guide: understanding INCLUDE-50 predictions

## What this app recognizes

This MVP submits one isolated signing clip to a fixed 50-class classifier. The official INCLUDE-50 map uses English class labels (glosses), such as `black`, `hello`, and `court`, with fixed class indices 0–49. These are not a vocabulary for full sentences, continuous signing, or arbitrary custom commands. The class label names do not describe the required hand shape or motion.

## How to read a result

- **Official class label/index**: the exact class name and index in AI4Bharat's INCLUDE-50 label map.
- **Model score**: the winning softmax score displayed by the model. It is uncalibrated and must not be interpreted as a guaranteed probability that the sign is correct.
- **Rejected**: the score did not meet the app's configured threshold (default 0.40 (provisional and uncalibrated)); the label is only a low-score candidate.
- **Accepted**: the score passed the configured threshold. This is an application gate only, not independent proof of correctness.
- **Other model candidates**: the model's five highest classes, shown to help explain ambiguity. Their scores are also uncalibrated.
- **Invalid capture**: quality checks rejected the clip before model inference; the app did not recognize a sign.

In the user's example, `black` at 28.48% with `rejected` means **the model's top guess was `black`, but the system did not accept it**. It should not be shown or described as a confirmed prediction.

## How to learn the physical sign

The class map tells you the label and class index; it is not a sign-language teaching resource. To verify the physical hand movement, compare the prediction against a labelled reference video from the INCLUDE dataset and/or ask a fluent ISL signer or instructor. Dataset video files are not bundled with this application.

- Official code and model label map: https://github.com/AI4Bharat/INCLUDE
- INCLUDE dataset page and video download information: https://huggingface.co/datasets/ai4bharat/INCLUDE
- Dataset record: https://zenodo.org/records/4010759

The dataset documentation says its videos were recorded in Chennai, Tamil Nadu and do not represent every regional variety of ISL. Do not treat this 50-class research model as a complete or universally valid ISL interpreter.

## Capture tips

1. Put your face/upper body and both hands where the webcam can see them, especially the signing hand.
2. Use good lighting and keep the hand inside the camera frame.
3. Capture one isolated sign, not a sentence or multiple signs in sequence.
4. If the capture is rejected by quality checks, adjust positioning and retry.
5. If the model result is rejected or ambiguous, compare the other candidates and reference sample; do not relax the threshold just to force acceptance.
6. Keep IoT disabled until real recognition has been validated.

## Developer verification

The web app exposes `GET /api/vocabulary`, backed by the application's validated official label map. The app does not synthesize sign images or invent gesture instructions.
