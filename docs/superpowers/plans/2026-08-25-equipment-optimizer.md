# Equipment Optimizer Implementation Plan

**Goal:** Build an independent SQLite-backed V0.1 equipment optimizer without changing the OCR workflow.

**Architecture:** Keep persistence, validation, rules, and optimization in focused Python modules. SQLite stores the nine documented entities; the optimizer loads typed records, computes static panels, scores simplified damage, and maintains Top-K legal builds.

**Tech Stack:** Python 3.12 standard library (`sqlite3`, `unittest`, `itertools`, `heapq`, `json`).

---

### Task 1: Add failing database and model tests

**Files:**
- Create: `tests/test_equipment_optimizer.py`

- [x] Write tests for schema initialization, idempotent initialization, foreign-key rejection, and model loading.
- [x] Write tests for panel calculation, crit cap/overflow, set activation, legal five-slot builds, and Top-K ordering.
- [x] Run `python -m unittest tests.test_equipment_optimizer -v`; confirmed the expected initial import failure.

### Task 2: Implement typed models and SQLite schema

**Files:**
- Create: `equipment_models.py`
- Create: `equipment_db.py`

- [x] Define slot/source/stat/effect enums and frozen dataclasses for heroes, profiles, items, sets, effects, scenarios, rules, panels, and results.
- [x] Define all nine tables with foreign keys, enum CHECK constraints, unique keys, numeric validation, and repeatable initialization.
- [x] Implement connection setup with `PRAGMA foreign_keys=ON`, schema initialization, typed row loaders, and transaction-safe insert helpers used by tests and future importers.
- [x] Run the database tests and make them pass.

### Task 3: Implement configurable V0.1 rules and data validation

**Files:**
- Create: `equipment_rules.py`
- Create: `equipment_data.py`

- [x] Implement rule loading from `game_rules`, defaults for attack composition, crit cap, attack-speed interval, defense multiplier, and source damage bonuses.
- [x] Implement validation for required records, decimal percentages, supported V0.1 effects, and profile share totals.
- [x] Run the focused rule and validation tests.

### Task 4: Implement panel calculation and Top-K search

**Files:**
- Create: `equipment_optimizer.py`

- [x] Aggregate equipment stats and static set effects.
- [x] Compute the static panel and simplified basic/skill/ultimate damage using scenario target limits and defense.
- [x] Enumerate left pair and right triple groups, combine legal full builds, and maintain sorted Top-K results with `delta_vs_rank1`.
- [x] Run optimizer tests and the full existing test suite.

### Task 5: Add runnable database initialization example and documentation

**Files:**
- Create: `init_equipment_db.py`
- Modify: `README.md`

- [x] Add a command-line initializer that creates `data/equipment.db` without requiring external database installation.
- [x] Document initialization, expected input path, and the fact that OCR integration is intentionally absent.
- [x] Run the initializer against a temporary database and rerun all tests.
