"""Tests for Dixon-Coles model."""

from app.models.dixon_coles_model import DixonColesModel


class TestDixonColesModel:
    def test_matrix_sums_to_one(self):
        model = DixonColesModel(default_rho=-0.13)
        pred = model.simulate(1.4, 1.1)
        assert abs(pred.score_matrix.sum() - 1.0) < 1e-9

    def test_rho_zero_near_independent_poisson(self):
        dc = DixonColesModel(default_rho=0.0)
        pred_dc = dc.simulate(1.3, 1.0, rho=0.0)
        assert 0.05 < pred_dc.probabilities["draw"] < 0.35
        assert pred_dc.probabilities["home_win"] > pred_dc.probabilities["away_win"]

    def test_negative_rho_increases_draw_mass_vs_rho_zero(self):
        zero = DixonColesModel(default_rho=0.0).simulate(1.2, 1.2, rho=0.0)
        neg = DixonColesModel(default_rho=-0.15).simulate(1.2, 1.2, rho=-0.15)
        assert neg.probabilities["draw"] >= zero.probabilities["draw"]

    def test_match_winner_extract(self):
        model = DixonColesModel()
        pred = model.simulate(1.5, 0.9)
        p_home = model._extract_probability(pred, "match_winner", "Home")
        p_draw = model._extract_probability(pred, "match_winner", "Draw")
        p_away = model._extract_probability(pred, "match_winner", "Away")
        assert p_home is not None and p_draw is not None and p_away is not None
        assert abs(p_home + p_draw + p_away - 1.0) < 1e-6

    def test_over_under_25(self):
        model = DixonColesModel()
        pred = model.simulate(0.8, 0.7)
        p_under = model._extract_probability(pred, "over_under", "Under 2.5", line=2.5)
        p_over = model._extract_probability(pred, "over_under", "Over 2.5", line=2.5)
        assert p_under is not None and p_over is not None
        assert abs(p_under + p_over - 1.0) < 1e-6

    def test_predict_from_features(self):
        model = DixonColesModel()
        features = {
            "home_venue_adjusted_xg": 1.4,
            "away_venue_adjusted_xg": 1.0,
            "home_injury_impact_score": 0.0,
            "away_injury_impact_score": 0.0,
            "league_id": 39,
        }
        prob = model.predict_from_features(features, "match_winner", "Draw")
        assert prob is not None
        assert 0.05 < prob < 0.45

    def test_missing_xg_returns_none(self):
        model = DixonColesModel()
        assert model.predict_from_features({}, "match_winner", "Home") is None
