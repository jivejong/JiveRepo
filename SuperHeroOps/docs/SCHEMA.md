# SuperHeroOps — Data Schema

EF Core entity definitions + the equivalent initial Postgres migration. Claude Code
should generate the actual `Migrations/` folder from these entities rather than
hand-applying the raw SQL below — it's included so the shape is unambiguous going in.

## Entities (`SuperHeroOps.Core`)

```csharp
public class CommunityArea
{
    public int Id { get; set; }                 // matches CPD's community area number (1-77)
    public required string Name { get; set; }

    public List<CrimeIncident> Incidents { get; set; } = [];
    public List<InterventionScore> Scores { get; set; } = [];
    public List<HeroReport> Reports { get; set; } = [];
}

public class CrimeIncident
{
    public long Id { get; set; }
    public required string CaseNumber { get; set; }
    public DateTime OccurredAt { get; set; }
    public required string PrimaryType { get; set; }   // IUCR category, e.g. "BATTERY"
    public bool Arrest { get; set; }
    public double? Latitude { get; set; }
    public double? Longitude { get; set; }

    public int CommunityAreaId { get; set; }
    public CommunityArea CommunityArea { get; set; } = null!;
}

public class Hero
{
    public int Id { get; set; }                  // SuperheroAPI's own id, reused as PK
    public required string Name { get; set; }
    public required string ImageUrl { get; set; }
    public string? Publisher { get; set; }
    public string? Alignment { get; set; }

    // Powerstats, 0-100 as returned by the API
    public int Intelligence { get; set; }
    public int Strength { get; set; }
    public int Speed { get; set; }
    public int Durability { get; set; }
    public int Power { get; set; }
    public int Combat { get; set; }

    public List<InterventionScore> Scores { get; set; } = [];
    public List<HeroReport> Reports { get; set; } = [];
}

public class InterventionScore
{
    public int Id { get; set; }
    public int CommunityAreaId { get; set; }
    public int HeroId { get; set; }

    public required string CrimeCategory { get; set; }   // e.g. "Violent", "Property"
    public double NormalizedScore { get; set; }           // 0-100, comparable across heroes
    public double ProjectedEffectPercent { get; set; }     // capped, illustrative only
    public DateTime ComputedAt { get; set; }

    public CommunityArea CommunityArea { get; set; } = null!;
    public Hero Hero { get; set; } = null!;
}

public class HeroReport
{
    public int Id { get; set; }
    public int CommunityAreaId { get; set; }
    public int HeroId { get; set; }

    // Stored as JSONB — parsed structured output from Groq
    public required string RiskAssessment { get; set; }
    public required string PredictedImpactNarrative { get; set; }
    public required string Recommendation { get; set; }
    public DateTime GeneratedAt { get; set; }

    public CommunityArea CommunityArea { get; set; } = null!;
    public Hero Hero { get; set; } = null!;
}
```

## Equivalent initial migration (Postgres)

```sql
CREATE TABLE community_areas (
    id          integer PRIMARY KEY,
    name        text NOT NULL
);

CREATE TABLE crime_incidents (
    id                  bigserial PRIMARY KEY,
    case_number         text NOT NULL,
    occurred_at         timestamp NOT NULL,
    primary_type        text NOT NULL,
    arrest              boolean NOT NULL DEFAULT false,
    latitude             double precision,
    longitude            double precision,
    community_area_id   integer NOT NULL REFERENCES community_areas(id)
);

CREATE INDEX idx_crime_incidents_community_area ON crime_incidents(community_area_id);
CREATE INDEX idx_crime_incidents_primary_type ON crime_incidents(primary_type);
CREATE INDEX idx_crime_incidents_occurred_at ON crime_incidents(occurred_at);

CREATE TABLE heroes (
    id              integer PRIMARY KEY,        -- reuse SuperheroAPI's own id
    name            text NOT NULL,
    image_url       text NOT NULL,
    publisher       text,
    alignment       text,
    intelligence    integer NOT NULL,
    strength        integer NOT NULL,
    speed           integer NOT NULL,
    durability      integer NOT NULL,
    power           integer NOT NULL,
    combat          integer NOT NULL
);

CREATE TABLE intervention_scores (
    id                          serial PRIMARY KEY,
    community_area_id           integer NOT NULL REFERENCES community_areas(id),
    hero_id                     integer NOT NULL REFERENCES heroes(id),
    crime_category               text NOT NULL,
    normalized_score             double precision NOT NULL,
    projected_effect_percent     double precision NOT NULL,
    computed_at                  timestamp NOT NULL DEFAULT now()
);

CREATE INDEX idx_intervention_scores_lookup
    ON intervention_scores(community_area_id, hero_id);

CREATE TABLE hero_reports (
    id                              serial PRIMARY KEY,
    community_area_id               integer NOT NULL REFERENCES community_areas(id),
    hero_id                         integer NOT NULL REFERENCES heroes(id),
    risk_assessment                 jsonb NOT NULL,
    predicted_impact_narrative      text NOT NULL,
    recommendation                  text NOT NULL,
    generated_at                    timestamp NOT NULL DEFAULT now()
);

CREATE INDEX idx_hero_reports_lookup
    ON hero_reports(community_area_id, hero_id);
```

## Notes for Claude Code

- `CommunityArea.Id` should be seeded from the Chicago Data Portal's "Boundaries -
  Community Areas" reference list (77 fixed areas, numbered) — load this once
  alongside the crime ingestion, not derived from crime records alone, so areas with
  zero incidents in the 90-day window still show up in the neighborhood list.
- `Hero.Id` intentionally reuses the SuperheroAPI numeric id rather than generating a
  new one — makes the seed file a direct 1:1 load with no id-mapping step.
- `InterventionScore` and `HeroReport` are both keyed by (community_area, hero) but
  kept as separate tables since one is deterministic/cheap to recompute and the other
  is an LLM call — don't collapse them, regenerating a report shouldn't require
  recomputing scores and vice versa.
- `crime_category` in `intervention_scores` is a free-text bucket label (e.g.
  "Violent"), not a foreign key to `primary_type` — it's the weight-map category from
  the handoff doc, not the raw IUCR type.
