namespace SuperHeroOps.Core.Reports;

// The fixed disclaimer every LLM-generated HeroReport must carry - injected by code
// after the model call, never left to model discretion. Appended into the persisted
// narrative itself (not just shown by the UI) so the disclaimer travels with the
// report content wherever it's read.
public static class HeroReportDisclaimer
{
    public const string Text = "This is a modeling exercise using fictional characters - not a policy forecast.";

    public static string AppendTo(string narrative)
        => string.IsNullOrWhiteSpace(narrative)
            ? Text
            : $"{narrative.TrimEnd()}\n\n{Text}";
}
