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
                       WHERE set_id='set_unshaken_will' AND available=1 AND locked=0
                       ORDER BY slot_id,item_id"""
                ).fetchall()
                raw_ids = {str(row["item_id"]) for row in raw_rows}

                projected_items = database.load_equipment()
                projected_unshaken = [
                    item for item in projected_items if item.set_id == "set_unshaken_will"
                ]
                projected_ids = {str(item.item_id) for item in projected_unshaken}
                projected_slots = {item.slot.value for item in projected_unshaken}
                self.assertTrue(raw_ids, "repository inventory should contain usable 不灭意志 items")
                self.assertTrue(
                    {"bracelet", "necklace", "ring"}.issubset(projected_slots),
                    "the visible 不灭意志 3-piece set must not disappear during projection",
                )
                self.assertTrue(
                    raw_ids.issubset(projected_ids),
                    f"usable 不灭意志 items disappeared during projection: {sorted(raw_ids - projected_ids)}",
                )
                self.assertFalse(
                    raw_ids.intersection(database.projection_exclusions),
                    "usable 不灭意志 items must not be excluded only because a locked P60 is unavailable",
                )
                reports = [
                    database.projection_reports[item_id]
                    for item_id in raw_ids
                    if item_id in database.projection_reports
                ]
                self.assertEqual(len(reports), len(raw_ids))
                self.assertTrue(
                    any(report.get("uses_locked_substat_zero_fallback") for report in reports),
                    "this regression inventory should exercise the conservative locked-stat fallback",
                )

                core = load_core("BROKKIR")
                profile = resolve_recommendation_profile(core)
                filtered_items, _ = prefilter_equipment(database, core, projected_items)
                filtered_unshaken = [
                    item for item in filtered_items if item.set_id == "set_unshaken_will"
                ]
                filtered_slots = {item.slot.value for item in filtered_unshaken}
                self.assertTrue(
                    {"bracelet", "necklace", "ring"}.issubset(filtered_slots),
                    "Brokkir prefilter must retain the defensively relevant 不灭意志 set",
                )

                by_slot = {slot: [] for slot in ("weapon", "armor", "bracelet", "necklace", "ring")}
                for item in filtered_items:
                    if item.slot.value in by_slot:
                        by_slot[item.slot.value].append(item)

                candidates, _ = _select_set_aware_candidates(
                    database, by_slot, 5, profile
                )
                candidate_unshaken_slots = {
                    slot for slot, items in candidates.items()
                    if any(item.set_id == "set_unshaken_will" for item in items)
                }
                self.assertTrue(
                    {"bracelet", "necklace", "ring"}.issubset(candidate_unshaken_slots),
                    "set-aware candidate pruning must retain one 不灭意志 item per right slot",
                )

                definitions = database.load_sets()
                evolutions = load_set_evolutions(database)
                set_names = load_set_names(database)
                effect_scores = _set_effect_scores(database, profile)
                right_builds, _ = _select_group_build_candidates(
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
                unshaken_complete = [
                    build for build in complete
                    if {item.set_id for item in build} == {"set_unshaken_will"}
                ]
                self.assertTrue(
                    unshaken_complete,
                    "the visible 不灭意志 bracelet+necklace+ring set must reach complete-set validation",
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
