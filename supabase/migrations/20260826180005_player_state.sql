-- Player latent-ability time series (SPEC.md section 5.5). The primary key
-- itself serves the as-of lookup pattern (seek to player_id+format, then
-- range/reverse-scan on as_of) - no extra index needed.

CREATE TABLE player_state (
  player_id           INT NOT NULL REFERENCES players(player_id),
  format              TEXT NOT NULL,
  as_of               DATE NOT NULL,
  bat_ability_mean    REAL,       -- latent skill, standardised
  bat_ability_sd      REAL,
  bowl_ability_mean   REAL,
  bowl_ability_sd     REAL,
  innings_observed    INT NOT NULL,
  PRIMARY KEY (player_id, format, as_of)
);
