-- Full-text search index backing GET /api/requisitions/{id}/matches.
--
-- This lives here rather than in a JPA annotation because @Table(indexes=...) cannot express a
-- functional index, and there are no Flyway migrations yet (docs/IMPLEMENTATION_PLAN.md leaves
-- schema versioning optional for v1). Runs after Hibernate creates the tables, thanks to
-- spring.jpa.defer-datasource-initialization, and IF NOT EXISTS makes it safe on every startup.
--
-- The to_tsvector expression must stay character-for-character identical to the one in
-- CandidateMatchRepository, or Postgres will not use this index and will scan sequentially.
CREATE INDEX IF NOT EXISTS idx_candidate_occupation_fts
    ON candidate
    USING GIN (to_tsvector('english', coalesce(occupation, '')));
