"""Model D: Neural net regressor for expected return."""

from pathlib import Path

import joblib
import numpy as np
from sklearn.preprocessing import StandardScaler

try:
    import torch
    import torch.nn as nn
    _TORCH_AVAILABLE = True
except ImportError:
    torch = None  # type: ignore[assignment]
    nn = None  # type: ignore[assignment]
    _TORCH_AVAILABLE = False

from app.config import get_settings
from app.utils.model_paths import resolve_trained_model
from app.predictions.probability_layer import probability_from_return
from app.training.target_selector import load_selected_transform
from app.training.targets import TargetTransform, denormalize_target

settings = get_settings()


if _TORCH_AVAILABLE:
    class ReturnNet(nn.Module):
        def __init__(self, input_dim: int, hidden: int = 32):
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(input_dim, hidden),
                nn.ReLU(),
                nn.Dropout(0.3),
                nn.Linear(hidden, 1),
            )

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            return self.net(x)
else:
    ReturnNet = None  # type: ignore[assignment,misc]


class NeuralNetworkModel:
    def __init__(self, model_path: Path | None = None):
        self.model_path = model_path or settings.model_dir / "neural_net.pkl"
        self.model: ReturnNet | None = None
        self.scaler: StandardScaler | None = None
        self.feature_names: list[str] = []
        self.enabled = True
        self.target_transform: TargetTransform = load_selected_transform()

    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        feature_names: list[str],
        X_val: np.ndarray | None = None,
        y_val: np.ndarray | None = None,
        epochs: int = 50,
        lr: float = 0.001,
        target_transform: TargetTransform | str | None = None,
    ) -> dict:
        self.feature_names = feature_names
        if isinstance(target_transform, str):
            self.target_transform = TargetTransform(target_transform)
        elif target_transform is not None:
            self.target_transform = target_transform
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)

        self.model = ReturnNet(X.shape[1])
        optimizer = torch.optim.Adam(self.model.parameters(), lr=lr, weight_decay=1e-4)
        criterion = nn.MSELoss()

        X_tensor = torch.FloatTensor(X_scaled)
        y_tensor = torch.FloatTensor(y).unsqueeze(1)

        val_x, val_y = None, None
        if X_val is not None and y_val is not None:
            val_x = torch.FloatTensor(self.scaler.transform(X_val))
            val_y = torch.FloatTensor(y_val).unsqueeze(1)

        best_val = float("inf")
        stale = 0
        self.model.train()
        for _ in range(epochs):
            optimizer.zero_grad()
            loss = criterion(self.model(X_tensor), y_tensor)
            loss.backward()
            optimizer.step()

            if val_x is not None and val_y is not None:
                self.model.eval()
                with torch.no_grad():
                    vl = criterion(self.model(val_x), val_y).item()
                self.model.train()
                if vl < best_val:
                    best_val = vl
                    stale = 0
                else:
                    stale += 1
                    if stale >= 10:
                        break

        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "state_dict": self.model.state_dict(),
                "scaler": self.scaler,
                "features": feature_names,
                "input_dim": X.shape[1],
                "target_transform": self.target_transform.value,
            },
            self.model_path,
        )
        return {"model": "neural_net", "samples": len(y), "best_val_mse": best_val}

    def load(self) -> bool:
        if not _TORCH_AVAILABLE:
            return False
        path = resolve_trained_model("neural_net.pkl")
        if path is None:
            return False
        data = joblib.load(path)
        self.feature_names = data["features"]
        self.scaler = data["scaler"]
        self.model = ReturnNet(data["input_dim"])
        self.model.load_state_dict(data["state_dict"])
        self.model.eval()
        if "target_transform" in data:
            self.target_transform = TargetTransform(data["target_transform"])
        self.model_path = path
        return True

    def predict_return(self, features: dict, odds: float = 2.0, rolling_std: float = 0.15) -> float:
        if not self.enabled:
            return 0.0
        if self.model is None and not self.load():
            return 0.0
        normalized = self.predict_norm(features)
        return denormalize_target(normalized, odds, rolling_std, self.target_transform)

    def predict_norm(self, features: dict) -> float:
        if not self.enabled:
            return 0.0
        if self.model is None and not self.load():
            return 0.0
        X = np.array([[features.get(f, 0.0) for f in self.feature_names]])
        with torch.no_grad():  # type: ignore[union-attr]
            return float(self.model(torch.FloatTensor(self.scaler.transform(X))).item())

    def predict_proba(self, features: dict, odds: float = 2.0) -> float | None:
        if not self.enabled:
            return None
        ret = self.predict_return(features, odds)
        return probability_from_return(ret, odds)
