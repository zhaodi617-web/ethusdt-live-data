import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import observer_v1_r as v


class Tests(unittest.TestCase):

    def fixture(self):
        base = 1_800_000_000_000
        m = []

        for i in range(25):
            t = base + i * v.M15
            price = 100 + i * 0.10
            m.append({
                "time": t,
                "open": str(price),
                "close": str(price + 0.10),
                "high": str(price + 0.18),
                "low": str(price - 0.18),
                "volume_usdt": "200",
                "confirmed": True,
            })

        h = []
        for i in range(10):
            t = base + (i - 7) * v.HOUR
            h.append({
                "time": t,
                "close": str(98 + i * 0.4),
                "confirmed": True,
            })

        now = m[-1]["time"] + v.M15 + 120_000

        candles = {
            "exchange": "OKX",
            "symbol": v.SYMBOL,
            "candles_15m": m,
            "candles_1h": h,
        }

        ticker = {
            "exchange": "OKX",
            "symbol": v.SYMBOL,
            "market_time": now - 1000,
            "price": "102.6",
        }

        return candles, ticker, now

    def test_six_horizons(self):
        c, t, n = self.fixture()
        prediction = v.make_prediction(c, t, n)

        self.assertEqual(
            [x["horizon_minutes"]
             for x in prediction["predictions"]],
            [15, 30, 45, 60, 120, 180],
        )
        self.assertFalse(prediction["live_order_allowed"])
        self.assertTrue(prediction["simulation_only"])

    def test_future_target_times(self):
        c, t, n = self.fixture()
        prediction = v.make_prediction(c, t, n)

        for item in prediction["predictions"]:
            self.assertEqual(
                item["target_time_ms"],
                n + item["horizon_minutes"] * 60_000,
            )

    def test_long_horizon_flags(self):
        c, t, n = self.fixture()
        prediction = v.make_prediction(c, t, n)

        for item in prediction["predictions"]:
            self.assertEqual(
                item["experimental_long_horizon"],
                item["horizon_minutes"] >= 120,
            )

    def test_original_weights_preserved(self):
        expected = {
            15: (0.80, 0.20),
            30: (0.65, 0.35),
            45: (0.45, 0.55),
            60: (0.30, 0.70),
        }

        for minutes, weights in expected.items():
            self.assertEqual(v.WEIGHTS[minutes], weights)

    def test_nonoverlapping_background(self):
        c, t, n = self.fixture()
        features = v.evaluate_features(c, t, n)

        self.assertLessEqual(
            features["hourly_background_last_end_ms"],
            c["candles_15m"][-1]["time"],
        )

        self.assertNotEqual(
            features["short_15m"],
            features["historical_nonoverlap_3h"],
        )

    def test_stale_quote_refused(self):
        c, t, n = self.fixture()
        t["market_time"] = n - 11 * 60_000

        with self.assertRaises(ValueError):
            v.make_prediction(c, t, n)

    def test_no_future_candle_verification(self):
        c, t, n = self.fixture()
        prediction = v.make_prediction(c, t, n)

        self.assertEqual(
            v.verify(
                [prediction], [], c, n + 4 * v.HOUR
            ),
            [],
        )

    def test_verification_first_close(self):
        c, t, n = self.fixture()
        prediction = v.make_prediction(c, t, n)

        candle_end = n + 20 * 60_000
        start = candle_end - v.M15

        c["candles_15m"].append({
            "time": start,
            "open": "102.6",
            "close": "103.0",
            "high": "103.1",
            "low": "102.5",
            "confirmed": True,
        })

        result = v.verify(
            [prediction], [], c, n + 31 * 60_000
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["horizon_minutes"], 15)
        self.assertEqual(
            result[0]["verification_time_ms"],
            candle_end,
        )

        self.assertEqual(
            v.verify(
                [prediction],
                result,
                c,
                n + 31 * 60_000,
            ),
            [],
        )

    def test_hourly_dedup_and_v1_untouched(self):
        c, t, n = self.fixture()

        with TemporaryDirectory() as td:
            base = Path(td)

            (base / "okx_candles.json").write_text(
                json.dumps(c)
            )
            (base / "okx_latest.json").write_text(
                json.dumps(t)
            )

            original = base / "observer_early_latest.json"
            original.write_text("DO NOT TOUCH")

            first = v.run(base, n)
            second = v.run(base, n + 10_000)

            self.assertEqual(first["new_predictions"], 1)
            self.assertEqual(second["new_predictions"], 0)
            self.assertEqual(
                original.read_text(), "DO NOT TOUCH"
            )

            records = v.read_lines(
                base / "v1_r_predictions.jsonl"
            )

            self.assertEqual(len(records), 1)
            self.assertEqual(
                len(records[0]["predictions"]), 6
            )

    def test_existing_history_not_rewritten(self):
        c, t, n = self.fixture()

        with TemporaryDirectory() as td:
            base = Path(td)

            (base / "okx_candles.json").write_text(
                json.dumps(c)
            )
            (base / "okx_latest.json").write_text(
                json.dumps(t)
            )

            # Build a valid fixture at the current timestamp.
            old = v.make_prediction(c, t, n)

            # Simulate an archived record from the prior hour.
            # This is test data only, not a real prediction.
            old["prediction_time_ms"] = n - v.HOUR
            old["rule_version"] = "legacy"

            old["predictions"] = [
                dict(
                    x,
                    target_time_ms=x["target_time_ms"] - v.HOUR,
                )
                for x in old["predictions"]
                if x["horizon_minutes"] <= 60
            ]

            path = base / "v1_r_predictions.jsonl"
            v.append_line(path, old)
            original_line = path.read_text()

            v.run(base, n)

            self.assertTrue(
                path.read_text().startswith(original_line)
            )

            records = v.read_lines(path)

            self.assertEqual(len(records), 2)
            self.assertEqual(
                len(records[0]["predictions"]), 4
            )
            self.assertEqual(
                len(records[1]["predictions"]), 6
            )


if __name__ == "__main__":
    unittest.main()
