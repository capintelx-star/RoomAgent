-- Migration 004: per-user rent tracking, leader role, and onboarding columns
ALTER TABLE users ADD COLUMN rent_amount_cents INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN is_leader INTEGER NOT NULL DEFAULT 0;
ALTER TABLE households ADD COLUMN rent_due_day INTEGER;
-- Records telegram_user_id of whoever ran /start so /join can grant is_leader
ALTER TABLE households ADD COLUMN leader_telegram_user_id INTEGER;
