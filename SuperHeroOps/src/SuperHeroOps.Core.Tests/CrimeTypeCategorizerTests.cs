using SuperHeroOps.Core.Scoring;
using Xunit;

namespace SuperHeroOps.Core.Tests;

public class CrimeTypeCategorizerTests
{
    [Theory]
    [InlineData("BATTERY", CrimeCategory.Violent)]
    [InlineData("ASSAULT", CrimeCategory.Violent)]
    [InlineData("ROBBERY", CrimeCategory.Violent)]
    [InlineData("THEFT", CrimeCategory.Property)]
    [InlineData("BURGLARY", CrimeCategory.Property)]
    [InlineData("MOTOR VEHICLE THEFT", CrimeCategory.Property)]
    [InlineData("CRIMINAL DAMAGE", CrimeCategory.CriminalDamage)]
    [InlineData("NARCOTICS", CrimeCategory.Narcotics)]
    [InlineData("WEAPONS VIOLATION", CrimeCategory.WeaponsViolation)]
    [InlineData("DECEPTIVE PRACTICE", CrimeCategory.DeceptivePractice)]
    public void Categorize_MapsKnownPrimaryTypesToExpectedCategory(string primaryType, CrimeCategory expected)
    {
        Assert.Equal(expected, CrimeTypeCategorizer.Categorize(primaryType));
    }

    [Theory]
    [InlineData("OTHER OFFENSE")]
    [InlineData("CRIMINAL TRESPASS")]
    [InlineData("PUBLIC PEACE VIOLATION")]
    [InlineData("CRIMINAL SEXUAL ASSAULT")]
    [InlineData("SEX OFFENSE")]
    [InlineData("SOMETHING NOT IN THE DATASET")]
    public void Categorize_ReturnsNullForUnmappedPrimaryTypes(string primaryType)
    {
        Assert.Null(CrimeTypeCategorizer.Categorize(primaryType));
    }

    [Fact]
    public void Categorize_IsCaseInsensitive()
    {
        Assert.Equal(CrimeCategory.Violent, CrimeTypeCategorizer.Categorize("battery"));
        Assert.Equal(CrimeCategory.Violent, CrimeTypeCategorizer.Categorize("Battery"));
    }
}
