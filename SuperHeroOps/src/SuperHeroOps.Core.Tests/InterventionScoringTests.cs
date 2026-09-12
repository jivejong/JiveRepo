using SuperHeroOps.Core.Scoring;
using Xunit;

namespace SuperHeroOps.Core.Tests;

public class InterventionScoringTests
{
    private static Hero MakeHero(
        int id,
        int intelligence = 0,
        int strength = 0,
        int speed = 0,
        int durability = 0,
        int power = 0,
        int combat = 0)
        => new()
        {
            Id = id,
            Name = $"Hero {id}",
            ImagePath = "images/heroes/test.jpg",
            Intelligence = intelligence,
            Strength = strength,
            Speed = speed,
            Durability = durability,
            Power = power,
            Combat = combat,
        };

    [Fact]
    public void RawScore_Violent_SumsStrengthCombatDurability()
    {
        var hero = MakeHero(1, strength: 10, combat: 20, durability: 30, intelligence: 999, speed: 999, power: 999);

        Assert.Equal(60, InterventionScoring.RawScore(hero, CrimeCategory.Violent));
    }

    [Fact]
    public void RawScore_Property_SumsSpeedIntelligence()
    {
        var hero = MakeHero(1, speed: 15, intelligence: 25, strength: 999, durability: 999, power: 999, combat: 999);

        Assert.Equal(40, InterventionScoring.RawScore(hero, CrimeCategory.Property));
    }

    [Fact]
    public void RawScore_CriminalDamage_SumsSpeedPower()
    {
        var hero = MakeHero(1, speed: 12, power: 18, intelligence: 999, strength: 999, durability: 999, combat: 999);

        Assert.Equal(30, InterventionScoring.RawScore(hero, CrimeCategory.CriminalDamage));
    }

    [Fact]
    public void RawScore_Narcotics_SumsIntelligencePower()
    {
        var hero = MakeHero(1, intelligence: 40, power: 10, strength: 999, speed: 999, durability: 999, combat: 999);

        Assert.Equal(50, InterventionScoring.RawScore(hero, CrimeCategory.Narcotics));
    }

    [Fact]
    public void RawScore_WeaponsViolation_SumsCombatStrength()
    {
        var hero = MakeHero(1, combat: 22, strength: 8, intelligence: 999, speed: 999, durability: 999, power: 999);

        Assert.Equal(30, InterventionScoring.RawScore(hero, CrimeCategory.WeaponsViolation));
    }

    [Fact]
    public void RawScore_DeceptivePractice_IsIntelligenceOnly()
    {
        var hero = MakeHero(1, intelligence: 77, strength: 999, speed: 999, durability: 999, power: 999, combat: 999);

        Assert.Equal(77, InterventionScoring.RawScore(hero, CrimeCategory.DeceptivePractice));
    }

    [Fact]
    public void NormalizeRoster_BestHeroInRosterScores100()
    {
        var strong = MakeHero(1, strength: 100, combat: 100, durability: 100);
        var weak = MakeHero(2, strength: 50, combat: 50, durability: 50);

        var normalized = InterventionScoring.NormalizeRoster([strong, weak], CrimeCategory.Violent);

        Assert.Equal(100.0, normalized[1]);
        Assert.Equal(50.0, normalized[2]);
    }

    [Fact]
    public void NormalizeRoster_IsRelativeToRosterNotTheoreticalMax()
    {
        // Neither hero comes close to the theoretical max of 300 (100+100+100),
        // but the better of the two should still normalize to exactly 100.
        var better = MakeHero(1, strength: 20, combat: 10, durability: 10);
        var worse = MakeHero(2, strength: 10, combat: 5, durability: 5);

        var normalized = InterventionScoring.NormalizeRoster([better, worse], CrimeCategory.Violent);

        Assert.Equal(100.0, normalized[1]);
        Assert.Equal(50.0, normalized[2]);
    }

    [Fact]
    public void NormalizeRoster_TiedHeroesBothScore100()
    {
        var a = MakeHero(1, strength: 30, combat: 30, durability: 30);
        var b = MakeHero(2, strength: 30, combat: 30, durability: 30);

        var normalized = InterventionScoring.NormalizeRoster([a, b], CrimeCategory.Violent);

        Assert.Equal(100.0, normalized[1]);
        Assert.Equal(100.0, normalized[2]);
    }

    [Fact]
    public void NormalizeRoster_SingleHeroScores100()
    {
        var solo = MakeHero(1, strength: 5, combat: 5, durability: 5);

        var normalized = InterventionScoring.NormalizeRoster([solo], CrimeCategory.Violent);

        Assert.Equal(100.0, normalized[1]);
    }

    [Fact]
    public void NormalizeRoster_AllZeroStatsAvoidsDivideByZero()
    {
        var a = MakeHero(1);
        var b = MakeHero(2);

        var normalized = InterventionScoring.NormalizeRoster([a, b], CrimeCategory.Violent);

        Assert.Equal(0.0, normalized[1]);
        Assert.Equal(0.0, normalized[2]);
    }

    [Fact]
    public void NormalizeRoster_EmptyRosterReturnsEmpty()
    {
        var normalized = InterventionScoring.NormalizeRoster([], CrimeCategory.Violent);

        Assert.Empty(normalized);
    }

    [Theory]
    [InlineData(0.0, 0.0)]
    [InlineData(50.0, 20.0)]
    [InlineData(100.0, 40.0)]
    public void ProjectedEffectPercent_IsLinearUpToCeiling(double normalizedScore, double expectedEffect)
    {
        Assert.Equal(expectedEffect, InterventionScoring.ProjectedEffectPercent(normalizedScore), precision: 6);
    }

    [Theory]
    [InlineData(-10.0)]
    [InlineData(150.0)]
    public void ProjectedEffectPercent_ClampsOutOfRangeInputs(double normalizedScore)
    {
        var effect = InterventionScoring.ProjectedEffectPercent(normalizedScore);

        Assert.InRange(effect, 0.0, InterventionScoring.MaxEffectCeilingPercent);
    }
}
