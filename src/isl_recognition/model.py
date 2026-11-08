from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

import torch


@dataclass(frozen=True)
class ModelMeta:
    feature_dim: int = 134
    max_frame_len: int = 169
    num_classes: int = 50
    hidden_size: int = 256
    attention_heads: int = 4
    hidden_layers: int = 2


class INCLUDETransformerSmall:
    """Thin adapter around the official AI4Bharat INCLUDE implementation."""

    def __init__(self, repo: Path, checkpoint: Path, device: str | None = None):
        self.repo = Path(repo)
        self.checkpoint_path = Path(checkpoint)
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        if not self.repo.exists():
            raise FileNotFoundError(f"AI4Bharat repository not found: {self.repo}")
        if not self.checkpoint_path.exists():
            raise FileNotFoundError(f"Checkpoint not found: {self.checkpoint_path}")

        repo_str = str(self.repo)
        if repo_str not in sys.path:
            sys.path.insert(0, repo_str)

        import transformers
        from transformers.models.bert.modeling_bert import BertLayer
        transformers.BertLayer = BertLayer

        from configs import TransformerConfig
        from models.transformer import Transformer

        self._Transformer = Transformer
        self._TransformerConfig = TransformerConfig
        self.model = Transformer(
            TransformerConfig(size="small"),
            n_classes=50,
        ).to(self.device)
        checkpoint = torch.load(
            self.checkpoint_path,
            map_location="cpu",
            weights_only=False,
        )
        state = self._extract_state_dict(checkpoint)
        self.model.load_state_dict(state, strict=True)
        self.model.eval()

    @staticmethod
    def _extract_state_dict(checkpoint):
        if not isinstance(checkpoint, dict):
            raise RuntimeError(f"Unexpected checkpoint type: {type(checkpoint)}")
        if "state_dict" in checkpoint:
            state = checkpoint["state_dict"]
        elif "model_state_dict" in checkpoint:
            state = checkpoint["model_state_dict"]
        elif isinstance(checkpoint.get("model"), dict):
            state = checkpoint["model"]
        else:
            state = checkpoint
        return {k.removeprefix("module."): v for k, v in state.items()}

    @torch.inference_mode()
    def predict_logits(self, model_input: torch.Tensor) -> torch.Tensor:
        if tuple(model_input.shape) != (1, 169, 134):
            raise ValueError(f"Expected input shape (1,169,134), got {tuple(model_input.shape)}")
        x = model_input.to(self.device, dtype=torch.float32)
        logits = self.model(x)
        if tuple(logits.shape) != (1, 50):
            raise RuntimeError(f"Expected output shape (1,50), got {tuple(logits.shape)}")
        if not torch.isfinite(logits).all():
            raise RuntimeError("Model produced NaN/Inf logits")
        return logits

    @torch.inference_mode()
    def predict(self, padded_sequence):
        x = torch.from_numpy(padded_sequence).unsqueeze(0).to(self.device)
        logits = self.predict_logits(x)
        probabilities = torch.softmax(logits, dim=1)[0]
        values, indices = torch.topk(probabilities, k=5)
        return {
            "predicted_index": int(indices[0].item()),
            "confidence": float(values[0].item()),
            "probabilities": [float(v) for v in probabilities.detach().cpu().tolist()],
            "top5_indices": [int(i) for i in indices.tolist()],
            "top5_probabilities": [float(v) for v in values.tolist()],
            "device": str(self.device),
        }
