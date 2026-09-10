package com.jivejong.springfieldtalentpipeline.ai;

import com.jivejong.springfieldtalentpipeline.candidate.Candidate;
import com.jivejong.springfieldtalentpipeline.config.GroqProperties;
import com.jivejong.springfieldtalentpipeline.requisition.Requisition;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;
import org.springframework.stereotype.Component;

/**
 * Builds the profile/fit-score generation call - docs/AI_FEATURES.md Feature 1.
 *
 * <p>Uses the smaller model: this is a short, fairly mechanical summarisation-and-scoring task, and
 * the faster model matters more here than raw capability.
 */
@Component
public class CandidateProfileGenerator {

    /** Catchphrases are personality flavour, not the substance - a few is plenty. */
    private static final int MAX_PHRASES = 5;

    /**
     * Kept free of per-candidate detail so it stays byte-identical across calls, which is what makes
     * it eligible for Groq's prompt caching (docs/AI_FEATURES.md).
     */
    private static final String SYSTEM_PROMPT =
            """
            You are an experienced technical recruiter writing an internal candidate profile for an \
            applicant tracking system. The candidates are fictional characters, and the exercise is \
            a portfolio demo - so treat their in-universe occupations and traits as real \
            professional history and assess them straight, without winking at the audience or \
            mentioning that they are fictional.

            For each candidate you will be given their known details and the role they are being \
            considered for. Produce:

            - bio: two or three sentences of narrative profile, grounded ONLY in the details \
            supplied. Do not invent employers, qualifications or dates. Write it as a recruiter \
            would for a colleague, not as marketing copy.
            - fitScore: an integer from 0 to 100 for how well this specific candidate suits this \
            specific role. Judge against the role's target keywords, not against how well known or \
            likeable the candidate is. Pick the band below that the candidate falls into, then a \
            value inside it - do not invent your own scale:

              90-100: the candidate's current occupation IS this role, or names the role's primary \
            keyword directly.
              70-89:  closely adjacent work - the core skill transfers and the candidate could do \
            this job now with little ramp-up.
              40-69:  partial overlap - the candidate matches the general field or one significant \
            keyword, but is missing the role's specific domain.
              10-39:  unrelated occupation, but a working adult who could be trained.
              0-9:    no relevant background at all, or plainly unable to hold the role.

            Apply the bands consistently: the same candidate and role must always land in the same \
            band. Where a candidate sits between two bands, choose the lower one.
            - fitRationale: one or two sentences justifying the score, naming the specific aspects \
            of the role's target keywords that the candidate does or does not meet.

            If a candidate's catchphrases suggest a temperament relevant to the role, you may use \
            that as colour, but never quote a catchphrase back as if it were an answer or a \
            qualification.
            """;

    private final GroqClient groqClient;
    private final GroqProperties properties;

    public CandidateProfileGenerator(GroqClient groqClient, GroqProperties properties) {
        this.groqClient = groqClient;
        this.properties = properties;
    }

    /** Structured reply - parsed straight into {@link AiCandidateProfile}. */
    public record ProfileGeneration(String bio, Integer fitScore, String fitRationale) {}

    public GroqClient.StructuredResult<ProfileGeneration> generate(
            Candidate candidate, Requisition requisition) {
        return groqClient.completeStructured(
                properties.getModels().getProfile(),
                SYSTEM_PROMPT,
                userPrompt(candidate, requisition),
                "candidate_profile",
                schema(),
                ProfileGeneration.class,
                properties.getTemperature().getProfile());
    }

    private static String userPrompt(Candidate candidate, Requisition requisition) {
        StringBuilder prompt = new StringBuilder();
        prompt.append("CANDIDATE\n");
        prompt.append("Name: ").append(candidate.getName()).append('\n');
        prompt.append("Occupation: ")
                .append(valueOrUnknown(candidate.getOccupation()))
                .append('\n');
        // Age is absent for most of the pool; say so rather than leaving the model to guess.
        prompt.append("Age: ")
                .append(candidate.getAge() == null ? "not on record" : candidate.getAge())
                .append('\n');
        List<String> phrases = candidate.getPhrases();
        if (phrases != null && !phrases.isEmpty()) {
            prompt.append("Known catchphrases: ")
                    .append(phrases.stream()
                            .limit(MAX_PHRASES)
                            .map(phrase -> '"' + phrase.replace('"', '\'') + '"')
                            .collect(Collectors.joining(", ")))
                    .append('\n');
        }

        prompt.append("\nROLE\n");
        prompt.append("Title: ").append(requisition.getTitle()).append('\n');
        prompt.append("Department: ")
                .append(valueOrUnknown(requisition.getDepartment()))
                .append('\n');
        prompt.append("Target keywords: ").append(requisition.getTargetKeywords()).append('\n');
        return prompt.toString();
    }

    private static String valueOrUnknown(String value) {
        return value == null || value.isBlank() ? "not on record" : value;
    }

    /** Schema the reply is constrained to, so parsing cannot be a guess. */
    private static Map<String, Object> schema() {
        Map<String, Object> bio = new LinkedHashMap<>();
        bio.put("type", "string");
        bio.put("description", "Two or three sentence narrative profile grounded in the details given");

        Map<String, Object> fitScore = new LinkedHashMap<>();
        fitScore.put("type", "integer");
        fitScore.put("minimum", 0);
        fitScore.put("maximum", 100);
        fitScore.put("description", "How well this candidate suits this specific role");

        Map<String, Object> fitRationale = new LinkedHashMap<>();
        fitRationale.put("type", "string");
        fitRationale.put(
                "description", "One or two sentences tied to the role's target keywords");

        Map<String, Object> propertiesNode = new LinkedHashMap<>();
        propertiesNode.put("bio", bio);
        propertiesNode.put("fitScore", fitScore);
        propertiesNode.put("fitRationale", fitRationale);

        Map<String, Object> schema = new LinkedHashMap<>();
        schema.put("type", "object");
        schema.put("properties", propertiesNode);
        schema.put("required", List.of("bio", "fitScore", "fitRationale"));
        schema.put("additionalProperties", false);
        return schema;
    }
}
