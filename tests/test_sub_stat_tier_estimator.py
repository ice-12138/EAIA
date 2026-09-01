import sqlite3
import unittest

from sub_stat_estimator import SubStatEstimator, normalize_set_tier


class TierAwareSubStatEstimatorTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE sets (
              set_id TEXT PRIMARY KEY,
              set_tier_id TEXT
            );
            CREATE TABLE equipment (
              item_id TEXT PRIMARY KEY,
              set_id TEXT,
              tier TEXT,
              quality_id TEXT
            );
            CREATE TABLE equipment_stats (
              item_id TEXT NOT NULL,
              stat_index INTEGER NOT NULL,
              stat_source TEXT NOT NULL,
              stat_type TEXT NOT NULL,
              stat_value REAL,
              is_unlocked INTEGER NOT NULL DEFAULT 1,
              roll_grade_id TEXT,
              value_confidence REAL NOT NULL DEFAULT 1.0,
              PRIMARY KEY(item_id, stat_index)
            );
            CREATE TABLE sub_stat_observations (
              item_id TEXT NOT NULL,
              stat_type TEXT NOT NULL,
              roll_grade_id TEXT NOT NULL,
              stat_value REAL NOT NULL,
              data_source TEXT NOT NULL,
              observed_at TEXT NOT NULL,
              PRIMARY KEY(item_id, stat_type, roll_grade_id)
            );
            CREATE TABLE stat_value_ranges (
              stat_type TEXT,
              stat_source TEXT,
              roll_grade_id TEXT,
              set_tier_id TEXT,
              min_value REAL,
              max_value REAL,
              verified_min REAL,
              verified_max REAL,
              observed_min REAL,
              observed_max REAL,
              data_source TEXT
            );
            """
        )
        self.connection.executemany(
            "INSERT INTO sets(set_id,set_tier_id) VALUES (?,?)",
            [("SET_T1", "T1"), ("SET_T2", "T2"), ("SET_T3", "T3"), ("SET_INF", "INF")],
        )
        self.connection.commit()

    def tearDown(self):
        self.connection.close()

    def _add_observations(self, set_id: str, prefix: str, values: list[float]) -> None:
        for index, value in enumerate(values):
            item_id = f"{prefix}_{index}"
            self.connection.execute(
                "INSERT INTO equipment(item_id,set_id,tier,quality_id) VALUES (?,?,?,?)",
                (item_id, set_id, "mythic_red", "mythic_red"),
            )
            self.connection.execute(
                """INSERT INTO equipment_stats(
                     item_id,stat_index,stat_source,stat_type,stat_value,
                     is_unlocked,roll_grade_id,value_confidence
                   ) VALUES (?,?,?,?,?,?,?,?)""",
                (item_id, 1, "sub", "CRIT_RATE", value, 1, None, 1.0),
            )
        self.connection.commit()

    def test_same_tier_iqr_filter_keeps_raw_reward_roll_but_excludes_it_from_estimate(self):
        values = [
            0.050, 0.161, 0.172, 0.178, 0.180, 0.185, 0.191,
            0.197, 0.203, 0.210, 0.214, 0.221, 0.387,
        ]
        self._add_observations("SET_T2", "T2", values)
        estimator = SubStatEstimator(self.connection, min_samples_for_estimation=10)

        estimate = estimator.estimate("CRIT_RATE", set_tier_id="T2")
        summary = estimator.distribution_summary("CRIT_RATE", "T2")

        self.assertAlmostEqual(estimate.low, 0.172)
        self.assertAlmostEqual(estimate.expected, 0.197)
        self.assertAlmostEqual(estimate.high, 0.214)
        self.assertEqual(estimate.source, "empirical_T2_iqr_p60")
        self.assertEqual(summary["raw_sample_count"], 13)
        self.assertEqual(summary["representative_sample_count"], 11)
        self.assertEqual(summary["filtered_sample_count"], 2)

        raw_reward_roll = self.connection.execute(
            "SELECT stat_value FROM sub_stat_observations WHERE item_id='T2_12'"
        ).fetchone()[0]
        self.assertAlmostEqual(raw_reward_roll, 0.387)

    def test_tiers_are_not_mixed(self):
        self._add_observations(
            "SET_T1",
            "T1",
            [0.101, 0.105, 0.109, 0.113, 0.117, 0.121, 0.125, 0.129, 0.133, 0.137],
        )
        self._add_observations(
            "SET_T2",
            "T2",
            [0.201, 0.205, 0.209, 0.213, 0.217, 0.221, 0.225, 0.229, 0.233, 0.237],
        )
        estimator = SubStatEstimator(self.connection, min_samples_for_estimation=10)

        t1 = estimator.estimate("CRIT_RATE", set_tier_id="T1")
        t2 = estimator.estimate("CRIT_RATE", set_tier_id="T2")
        t3 = estimator.estimate("CRIT_RATE", set_tier_id="T3")

        self.assertLess(t1.expected, t2.expected)
        self.assertIsNone(t3.expected)
        self.assertEqual(t3.source, "insufficient_data")

    def test_inf_is_grouped_with_t3(self):
        self.assertEqual(normalize_set_tier("INF"), "T3")
        self.assertEqual(normalize_set_tier("inf_ancient"), "T3")

        self._add_observations(
            "SET_INF",
            "INF",
            [0.301, 0.305, 0.309, 0.313, 0.317, 0.321, 0.325, 0.329, 0.333, 0.337],
        )
        estimator = SubStatEstimator(self.connection, min_samples_for_estimation=10)
        estimate = estimator.estimate("CRIT_RATE", set_tier_id="T3")
        self.assertIsNotNone(estimate.expected)
        self.assertEqual(estimate.source, "empirical_T3_iqr_p60")

    def test_real_extreme_observation_is_stored_not_queued_as_failure(self):
        self.connection.execute(
            "INSERT INTO equipment(item_id,set_id,tier,quality_id) VALUES ('REWARD','SET_T2','mythic_red','mythic_red')"
        )
        self.connection.commit()
        estimator = SubStatEstimator(self.connection)
        result = estimator.observe(
            item_id="REWARD",
            stat_type="CRIT_RATE",
            roll_grade_id="unknown",
            value=38.7,
            data_source="ocr",
            ocr_confidence=0.99,
        )
        self.assertEqual(result, "observed")
        stored = self.connection.execute(
            "SELECT stat_value,set_tier_id FROM sub_stat_observations WHERE item_id='REWARD'"
        ).fetchone()
        self.assertAlmostEqual(stored[0], 0.387)
        self.assertEqual(stored[1], "T2")
        queued = self.connection.execute(
            "SELECT COUNT(*) FROM stat_observation_queue WHERE item_id='REWARD'"
        ).fetchone()[0]
        self.assertEqual(queued, 0)


if __name__ == "__main__":
    unittest.main()
