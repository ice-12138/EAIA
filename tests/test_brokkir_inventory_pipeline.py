import shutil
import tempfile
import unittest
from pathlib import Path

from equipment_recommendation_prefilter import prefilter_equipment
from equipment_recommendation_profile import resolve_recommendation_profile
from equipment_set_variants import load_set_evolutions, load_set_names
from hero_core_engine import load_core
from hero_core_service import (
    _RIGHT_SLOT_ORDER,
    _group_can_reach_complete_set,
    _select_group_build_candidates,
    _select_set_aware_candidates,
    _set_effect_scores,
    recommend_hero_core,
)
from optimizer_projection import OptimizerEquipmentDatabase


class BrokkirInventoryPipelineTests(unittest.TestCase):
    @staticmethod
    def _repository_database_copy(directory):
        source = Path(__file__).resolve().parents[1] / "data" / "equipment.db"
        if not source.exists():
            raise unittest.SkipTest("repository equipment database is unavailable")
        copied = Path(directory) / "equipment.db"
        shutil.copyfile(source, copied)
        return copied

    def test_repository_unshaken_complete_set_survives_brokkir_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            copied = self._repository_database_copy(directory)
            database = OptimizerEquipmentDatabase(copied, percentile=0.60)
            try:
                database.initialize()
                raw_rows = database.connection.execute(
                    """SELECT item_id,COALESCE(slot_id,slot) AS slot_id,set_id,
                              available,locked,item_locked,quality_id,enhancement_level
                       FROM equipment
                       WHERE set_id='set_unshaken_will'
                       ORDER BY slot_id,item_id"""
                ).fetchall()
                print("RAW UNSHAKEN ITEMS:", [dict(row) for row in raw_rows])
                raw_ids = [str(row["item_id"]) for row in raw_rows]
                for item_id in raw_ids:
                    stats = database.connection.execute(
                        """SELECT stat_index,stat_source,stat_type,stat_value,
                                  is_unlocked,roll_grade_id,estimate_override
                           FROM equipment_stats WHERE item_id=? ORDER BY stat_index""",
                        (item_id,),
                    ).fetchall()
                    print("RAW STATS", item_id, [dict(row) for row in stats])

                projected_items = database.load_equipment()
                projected_unshaken = [
                    item for item in projected_items if item.set_id == "set_unshaken_will"
                ]
                print(
                    "PROJECTED UNSHAKEN:",
                    [(item.item_id, item.slot.value) for item in projected_unshaken],
                )
                print(
                    "UNSHAKEN PROJECTION EXCLUSIONS:",
                    {item_id: database.projection_exclusions.get(item_id) for item_id in raw_ids
                     if item_id in database.projection_exclusions},
                )

                core = load_core("BROKKIR")
                profile = resolve_recommendation_profile(core)
                filtered_items, report = prefilter_equipment(database, core, projected_items)
                filtered_unshaken = [
                    item for item in filtered_items if item.set_id == "set_unshaken_will"
                ]
                print("BROKKIR PROFILE:", profile)
                print(
                    "PREFILTER UNSHAKEN:",
                    [(item.item_id, item.slot.value) for item in filtered_unshaken],
                )
                removed = report.get("removed_item_ids") or {}
                print(
                    "UNSHAKEN PREFILTER REMOVALS:",
                    {reason: [item_id for item_id in ids if item_id in raw_ids]
                     for reason, ids in removed.items()
                     if any(item_id in raw_ids for item_id in ids)},
                )

                by_slot = {slot: [] for slot in ("weapon", "armor", "bracelet", "necklace", "ring")}
                for item in filtered_items:
                    if item.slot.value in by_slot:
                        by_slot[item.slot.value].append(item)
                print("FILTERED SLOT COUNTS:", {slot: len(items) for slot, items in by_slot.items()})

                candidates, _ = _select_set_aware_candidates(
                    database, by_slot, 5, profile
                )
                print(
                    "CANDIDATE UNSHAKEN:",
                    {slot: [item.item_id for item in items if item.set_id == "set_unshaken_will"]
                     for slot, items in candidates.items()},
                )

                definitions = database.load_sets()
                evolutions = load_set_evolutions(database)
                set_names = load_set_names(database)
                effect_scores = _set_effect_scores(database, profile)
                right_builds, right_raw = _select_group_build_candidates(
                    candidates,
                    _RIGHT_SLOT_ORDER,
                    16,
                    definitions=definitions,
                    effect_scores=effect_scores,
                    evolutions=evolutions,
                    set_names=set_names,
                    recommendation_profile=profile,
                )
                complete = [
                    build for build in right_builds
                    if _group_can_reach_complete_set(
                        tuple(build),
                        slot_order=_RIGHT_SLOT_ORDER,
                        expected_group="right",
                        definitions=definitions,
                        evolutions=evolutions,
                        set_names=set_names,
                    )
                ]
                print("RIGHT RAW/KEPT/COMPLETE:", right_raw, len(right_builds), len(complete))
                print(
                    "COMPLETE RIGHT BUILDS:",
                    [[(item.item_id, item.set_id, item.slot.value) for item in build]
                     for build in complete[:20]],
                )

                self.assertTrue(raw_ids, "repository inventory should contain 不灭意志 items")
                self.assertTrue(
                    complete,
                    "repository inventory has a complete right set, but Brokkir pipeline removes it",
                )
            finally:
                database.close()

    def test_exact_brokkir_sets_only_recommendation_on_repository_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            copied = self._repository_database_copy(directory)
            result = recommend_hero_core(
                copied,
                {
                    "hero_core_id": "BROKKIR",
                    "sets_only": True,
                    "top_k": 1,
                    "candidate_per_slot": 5,
                    "trials": 1,
                    "screening_trials": 1,
                    "screening_warmup": 0,
                    "screening_measurement": 1,
                    "warmup": 0,
                    "measurement": 1,
                    "seed": 20260902,
                },
            )
            self.assertTrue(result["results"])
            best = result["results"][0]
            self.assertTrue(best["sets_only"])
            final_states = best["final_set_states"]
            by_slot = {row["slot"]: row for row in final_states}
            self.assertEqual(set(by_slot), {"weapon", "armor", "bracelet", "necklace", "ring"})
            self.assertEqual(by_slot["weapon"]["set_id"], by_slot["armor"]["set_id"])
            self.assertEqual(
                by_slot["bracelet"]["set_id"],
                by_slot["necklace"]["set_id"],
            )
            self.assertEqual(
                by_slot["bracelet"]["set_id"],
                by_slot["ring"]["set_id"],
            )


if __name__ == "__main__":
    unittest.main()
