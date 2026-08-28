import copy
import tempfile
import unittest
from pathlib import Path

from equipment_db import EquipmentDatabase
from hero_core_engine import load_core
from hero_core_service import hero_core_codex_payload, import_hero_core, recommend_hero_core


class HeroCoreServiceTests(unittest.TestCase):
    def test_imported_core_exposes_attributes_and_skills_in_codex(self):
        with tempfile.TemporaryDirectory() as directory:
            core = copy.deepcopy(load_core("SUN_WUKONG"))
            core["hero"]["id"] = "UPLOAD_TEST"
            core["hero"]["name"] = "上传测试英雄"
            result = import_hero_core({"core": core}, Path(directory))
            self.assertEqual(result["hero_core_id"], "UPLOAD_TEST")
            payload = hero_core_codex_payload(Path(directory))
            hero = payload["heroes"][0]
            self.assertEqual(hero["hero_name"], "上传测试英雄")
            self.assertEqual(hero["base_stats"]["atk"], core["hero"]["base_stats"]["atk"])
            self.assertGreaterEqual(len(payload["skills"]), 4)

    def test_recommendation_uses_hero_core_with_one_item_per_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "equipment.db"
            database = EquipmentDatabase(database_path)
            try:
                database.initialize()
                database.connection.execute(
                    "INSERT OR IGNORE INTO sets(set_id,set_name,required_pieces,slot_group,output_set) VALUES ('TEST_NONE','测试无套装',99,NULL,1)"
                )
                for slot in ("weapon", "armor", "bracelet", "necklace", "ring"):
                    database.connection.execute(
                        """INSERT INTO equipment(item_id,slot,set_id,locked,available,slot_id,enhancement_level,item_locked)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (f"TEST_{slot}", slot, "TEST_NONE", 0, 1, slot, 16, 0),
                    )
                database.connection.commit()
            finally:
                database.close()

            result = recommend_hero_core(database_path, {
                "hero_core_id": "SUN_WUKONG",
                "top_k": 1,
                "candidate_per_slot": 1,
                "trials": 1,
                "screening_warmup": 0,
                "screening_measurement": 60,
                "warmup": 0,
                "measurement": 60,
                "target_def": 0,
            })
            self.assertEqual(result["combinations_screened"], 1)
            self.assertEqual(len(result["results"]), 1)
            self.assertGreater(result["results"][0]["equivalent_60s"]["mean"], 0)
            self.assertEqual(len(result["results"][0]["item_ids"]), 5)


if __name__ == "__main__":
    unittest.main()
