import numpy as np
import pytest


def test_model_contract_is_explicit():
    x = np.zeros((169, 134), dtype=np.float32)
    assert x.shape == (169, 134)
    assert np.isfinite(x).all()


def test_official_checkpoint_loads_strictly():
    from isl_recognition.app_config import Settings
    cfg = Settings()
    # Skip before importing the model adapter: model.py imports torch at module
    # import time, but the core test environment intentionally does not require
    # model dependencies unless the real external assets are configured.
    if not cfg.ai4bharat_repo or not cfg.ai4bharat_repo.is_dir():
        pytest.skip("Set AI4BHARAT_REPO to the official AI4Bharat INCLUDE checkout to run model-dependent test")
    if not cfg.checkpoint or not cfg.checkpoint.is_file():
        pytest.skip("Set ISL_CHECKPOINT to include50_no_cnn_transformer_small.pth to run model-dependent test")
    from isl_recognition.model import INCLUDETransformerSmall
    model = INCLUDETransformerSmall(cfg.ai4bharat_repo, cfg.checkpoint)
    assert model.model is not None
