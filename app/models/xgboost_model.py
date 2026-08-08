"""Model C: XGBoost regressor — predicts expected return per unit staked."""

from pathlib import Path

import joblib
import numpy as np
import optuna
import xgboost as xgb

from app.config import get_settings
from app.utils.model_paths import resolve_trained_model
from app.predictions.probability_layer import probability_from_return
from app.training.target_selector import load_selected_transform
from app.training.targets import TargetTransform, denormalize_target
from app.training.validation import time_series_cv

settings = get_settings()


class XGBoostModel:
    """Trained on realized_return (continuous), NOT binary win/lose."""

    def __init__(self, model_path: Path | None = None):
        self.model_path = model_path or settings.model_dir / "xgboost.pkl"
        self.model: xgb.XGBRegressor | None = None
        self.feature_names: list[str] = []
        self.target_transform: TargetTransform = load_selected_transform()

    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        feature_names: list[str],
        optimize: bool = True,
        target_transform: TargetTransform | None = None,
    ) -> dict:
        self.feature_names = feature_names
        self.target_transform = target_transform or load_selected_transform()
        params = self._optimize_hyperparams(X, y) if optimize and len(y) >= 30 else {
            "n_estimators": 200,
            "learning_rate": 0.05,
            "max_depth": 5,
            "min_child_weight": 5,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "reg_lambda": 1.0,
        }

        self.model = xgb.XGBRegressor(objective="reg:squarederror", verbosity=0, **params)
        self.model.fit(X, y)

        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({
            "model": self.model,
            "features": feature_names,
            "target_transform": self.target_transform.value,
        }, self.model_path)
        return {"model": "xgboost", "samples": len(y), "params": params}

    def _optimize_hyperparams(self, X: np.ndarray, y: np.ndarray) -> dict:
        tscv = time_series_cv(n_splits=min(3, max(2, len(y) // 20)))

        def objective(trial: optuna.Trial) -> float:
            params = {
                "n_estimators": trial.suggest_int("n_estimators", 100, 400),
                "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.15, log=True),
                "max_depth": trial.suggest_int("max_depth", 3, 8),
                "min_child_weight": trial.suggest_int("min_child_weight", 3, 15),
                "subsample": trial.suggest_float("subsample", 0.6, 1.0),
                "colsample_bytree": trial.suggest_float("colsample_bytree", 0.6, 1.0),
                "reg_lambda": trial.suggest_float("reg_lambda", 0.1, 5.0),
            }
            scores = []
            for train_idx, val_idx in tscv.split(X):
                model = xgb.XGBRegressor(objective="reg:squarederror", verbosity=0, **params)
                model.fit(X[train_idx], y[train_idx])
                pred = model.predict(X[val_idx])
                scores.append(float(np.mean((pred - y[val_idx]) ** 2)))
            return np.mean(scores)

        study = optuna.create_study(direction="minimize")
        study.optimize(objective, n_trials=min(20, max(5, len(y) // 10)), show_progress_bar=False)
        return study.best_params

    def load(self) -> bool:
        path = resolve_trained_model("xgboost.pkl")
        if path is None:
            return False
        data = joblib.load(path)
        self.model = data["model"]
        self.feature_names = data["features"]
        if "target_transform" in data:
            self.target_transform = TargetTransform(data["target_transform"])
        self.model_path = path
        return True

    def predict_return(self, features: dict, odds: float = 2.0, rolling_std: float = 0.15) -> float:
        if self.model is None and not self.load():
            return 0.0
        X = np.array([[features.get(f, 0.0) for f in self.feature_names]])
        normalized = float(self.model.predict(X)[0])
        return denormalize_target(normalized, odds, rolling_std, self.target_transform)

    def predict_norm(self, features: dict) -> float:
        if self.model is None and not self.load():
            return 0.0
        X = np.array([[features.get(f, 0.0) for f in self.feature_names]])
        return float(self.model.predict(X)[0])

    def predict_proba(self, features: dict, odds: float = 2.0) -> float | None:
        if self.model is None and not self.load():
            return None
        ret = self.predict_return(features, odds)
        return probability_from_return(ret, odds)
