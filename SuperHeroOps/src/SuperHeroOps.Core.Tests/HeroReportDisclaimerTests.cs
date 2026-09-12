using SuperHeroOps.Core.Reports;
using Xunit;

namespace SuperHeroOps.Core.Tests;

public class HeroReportDisclaimerTests
{
    [Fact]
    public void AppendTo_AppendsDisclaimerAfterNarrative()
    {
        var result = HeroReportDisclaimer.AppendTo("Batman would likely reduce burglary calls.");

        Assert.StartsWith("Batman would likely reduce burglary calls.", result);
        Assert.EndsWith(HeroReportDisclaimer.Text, result);
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData(null)]
    public void AppendTo_HandlesEmptyNarrative(string? narrative)
    {
        var result = HeroReportDisclaimer.AppendTo(narrative!);

        Assert.Equal(HeroReportDisclaimer.Text, result);
    }
}
