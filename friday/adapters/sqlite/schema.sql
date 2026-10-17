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
