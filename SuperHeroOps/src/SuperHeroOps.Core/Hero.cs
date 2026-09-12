namespace SuperHeroOps.Core;

public class Hero
{
    public int Id { get; set; } // SuperheroAPI's own id, reused as PK
    public required string Name { get; set; }

    // Repo-relative path under Web/wwwroot (e.g. "images/heroes/70.jpg"), not a remote
    // URL. Null for every hero currently - SuperheroAPI's image CDN sits behind a
    // Cloudflare bot challenge that blocks non-browser HTTP clients regardless of
    // headers or source IP, so portrait download was dropped rather than worked
    // around. Left nullable/wired through the UI so a future fix (e.g. browser
    // automation) doesn't require another schema or UI change.
    public string? ImagePath { get; set; }
    public string? Publisher { get; set; }
    public string? Alignment { get; set; }

    // Powerstats, 0-100 as returned by the API
    public int Intelligence { get; set; }
    public int Strength { get; set; }
    public int Speed { get; set; }
    public int Durability { get; set; }
    public int Power { get; set; }
    public int Combat { get; set; }

    // Biographical fields from SuperheroAPI. Real structured data about the
    // character, not a powers/abilities description - SuperheroAPI doesn't provide
    // the latter, and none is fabricated for the "Hero Details" display.
    public string? FullName { get; set; }
    public string? Aliases { get; set; } // comma-separated
    public string? PlaceOfBirth { get; set; }
    public string? FirstAppearance { get; set; }

    public string? Height { get; set; } // e.g. "6'2 (188 cm)"
    public string? Weight { get; set; } // e.g. "210 lb (95 kg)"
    public string? EyeColor { get; set; }
    public string? HairColor { get; set; }

    public string? Occupation { get; set; }
    public string? Base { get; set; }
    public string? GroupAffiliation { get; set; }

    public List<InterventionScore> Scores { get; set; } = [];
    public List<HeroReport> Reports { get; set; } = [];
}
