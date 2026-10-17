-- friday.db: high-volume run state. Ledger and events are append-only.
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS passes (
  id TEXT PRIMARY KEY, round INTEGER NOT NULL, category TEXT NOT NULL, arm TEXT NOT NULL,
  worker TEXT NOT NULL, regime TEXT NOT NULL, composition TEXT NOT NULL,   -- composition: JSON
  pair_slot INTEGER, artifact_path TEXT, repaired INTEGER NOT NULL DEFAULT 0,
  transport_failed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS evals (
  pass_id TEXT PRIMARY KEY REFERENCES passes(id), regime TEXT NOT NULL, checks TEXT NOT NULL,
  console_errors TEXT NOT NULL, a REAL NOT NULL, r REAL NOT NULL, rubric TEXT NOT NULL,
  q INTEGER NOT NULL, eslint_errors INTEGER NOT NULL, axe_violations INTEGER NOT NULL, loss REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS lens_answers (
  pass_id TEXT NOT NULL, lens_id TEXT NOT NULL, p REAL NOT NULL, quote TEXT NOT NULL,
  judge_model TEXT NOT NULL, PRIMARY KEY (pass_id, lens_id)
);
CREATE TABLE IF NOT EXISTS fires (
  pass_id TEXT NOT NULL, decision_id TEXT NOT NULL, round INTEGER NOT NULL, disposition TEXT NOT NULL, quote TEXT NOT NULL,
  PRIMARY KEY (pass_id, decision_id)
);
CREATE TABLE IF NOT EXISTS observations (
  id TEXT PRIMARY KEY, pass_id TEXT NOT NULL, round INTEGER NOT NULL, lens_id TEXT NOT NULL,
  category TEXT NOT NULL, shape TEXT NOT NULL, direction TEXT NOT NULL, quote TEXT NOT NULL,
  status TEXT NOT NULL, pool_id TEXT, direction_label TEXT
);
CREATE TABLE IF NOT EXISTS pools (
  id TEXT PRIMARY KEY, lens_id TEXT NOT NULL, shape TEXT NOT NULL, direction_labels TEXT NOT NULL,
  status TEXT NOT NULL, merged_into TEXT, refused_at_round INTEGER
);
CREATE TABLE IF NOT EXISTS ledger_deltas (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, decision_id TEXT NOT NULL, round INTEGER NOT NULL,
  category TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('exposure', 'ablation')),
  d REAL NOT NULL CHECK (d BETWEEN -1 AND 1), dq REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ledger_by_decision ON ledger_deltas(decision_id, kind, seq);
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, subject_id TEXT NOT NULL,
  round INTEGER NOT NULL, body TEXT NOT NULL, applied_round INTEGER
);
CREATE TABLE IF NOT EXISTS spend (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, role TEXT NOT NULL, tag TEXT NOT NULL,
  model TEXT, tokens_in INTEGER, tokens_out INTEGER, cost_usd REAL, ok INTEGER NOT NULL, error TEXT,
  sent TEXT
);
-- Triggers keep the evidence append-only. Only a steer's applied_round may be set, once.
CREATE TRIGGER IF NOT EXISTS events_no_rewrite BEFORE UPDATE OF type, subject_id, round, body ON events
  BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_applied_once BEFORE UPDATE OF applied_round ON events
  WHEN OLD.applied_round IS NOT NULL OR OLD.type != 'steer'
  BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
  BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger_deltas
  BEGIN SELECT RAISE(ABORT, 'ledger_deltas is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger_deltas
  BEGIN SELECT RAISE(ABORT, 'ledger_deltas is append-only'); END;

-- Additions for the pass and judge stores, the register stores and the exposure store (CARRY_FORWARD S6).
-- The readings of an EvalReport that `evals` has no column for: P, H, C, silent controls and the nested
-- probe and how-to readings (JSON), so the report rebuilds field for field.
CREATE TABLE IF NOT EXISTS eval_details (
  pass_id TEXT PRIMARY KEY REFERENCES evals(pass_id), p REAL NOT NULL, h REAL NOT NULL, c REAL NOT NULL,
  silent_controls INTEGER NOT NULL, probes TEXT NOT NULL, how_to TEXT NOT NULL
);
-- A round's pair plans, as drawn before its passes run. `exposure_rounds` marks a round recorded, so a
-- round with no plans is still recorded once.
CREATE TABLE IF NOT EXISTS exposure_rounds (round INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS exposures (
  round INTEGER NOT NULL REFERENCES exposure_rounds(round), seq INTEGER NOT NULL, category TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('exposure', 'ablation', 'none')), decision_id TEXT,
  treated_slot INTEGER NOT NULL CHECK (treated_slot IN (0, 1)), PRIMARY KEY (round, seq)
);
-- One finished holder per pair slot: two passes of one round, category, request and arm cannot both
-- record the same slot. A pass failed for transport gives no delta and leaves its slot to the re-run.
CREATE UNIQUE INDEX IF NOT EXISTS passes_one_holder_per_slot
  ON passes(round, category, arm, json_extract(composition, '$.request_id'), pair_slot)
  WHERE pair_slot IS NOT NULL AND transport_failed = 0;
CREATE INDEX IF NOT EXISTS passes_by_round ON passes(round);
CREATE INDEX IF NOT EXISTS fires_by_decision ON fires(decision_id);
-- A pass's rows (pass, eval, lens answers, fires) and the exposure plans are insert-only.
CREATE TRIGGER IF NOT EXISTS passes_no_update BEFORE UPDATE ON passes
  BEGIN SELECT RAISE(ABORT, 'passes is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS passes_no_delete BEFORE DELETE ON passes
  BEGIN SELECT RAISE(ABORT, 'passes is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS evals_no_update BEFORE UPDATE ON evals
  BEGIN SELECT RAISE(ABORT, 'evals is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS evals_no_delete BEFORE DELETE ON evals
  BEGIN SELECT RAISE(ABORT, 'evals is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS eval_details_no_update BEFORE UPDATE ON eval_details
  BEGIN SELECT RAISE(ABORT, 'eval_details is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS eval_details_no_delete BEFORE DELETE ON eval_details
  BEGIN SELECT RAISE(ABORT, 'eval_details is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS lens_answers_no_update BEFORE UPDATE ON lens_answers
  BEGIN SELECT RAISE(ABORT, 'lens_answers is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS lens_answers_no_delete BEFORE DELETE ON lens_answers
  BEGIN SELECT RAISE(ABORT, 'lens_answers is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS fires_no_update BEFORE UPDATE ON fires
  BEGIN SELECT RAISE(ABORT, 'fires is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS fires_no_delete BEFORE DELETE ON fires
  BEGIN SELECT RAISE(ABORT, 'fires is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS exposure_rounds_no_update BEFORE UPDATE ON exposure_rounds
  BEGIN SELECT RAISE(ABORT, 'exposures is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS exposure_rounds_no_delete BEFORE DELETE ON exposure_rounds
  BEGIN SELECT RAISE(ABORT, 'exposures is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS exposures_no_update BEFORE UPDATE ON exposures
  BEGIN SELECT RAISE(ABORT, 'exposures is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS exposures_no_delete BEFORE DELETE ON exposures
  BEGIN SELECT RAISE(ABORT, 'exposures is insert-only'); END;
-- Observations and pools are registers, corrected in place, with ids reserved once: never deleted.
-- An observation's facts from its pass (pass, round, category, quote) never change.
CREATE TRIGGER IF NOT EXISTS observations_facts_frozen BEFORE UPDATE ON observations
  WHEN NEW.pass_id IS NOT OLD.pass_id OR NEW.round IS NOT OLD.round OR NEW.category IS NOT OLD.category
    OR NEW.quote IS NOT OLD.quote
  BEGIN SELECT RAISE(ABORT, 'an observation''s pass, round, category and quote are frozen'); END;
CREATE TRIGGER IF NOT EXISTS observations_no_delete BEFORE DELETE ON observations
  BEGIN SELECT RAISE(ABORT, 'observation ids are reserved once'); END;
CREATE TRIGGER IF NOT EXISTS pools_no_delete BEFORE DELETE ON pools
  BEGIN SELECT RAISE(ABORT, 'pool ids are reserved once'); END;
