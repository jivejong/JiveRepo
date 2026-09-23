package com.jivejong.springfieldtalentpipeline.ai;

import com.jivejong.springfieldtalentpipeline.candidate.Candidate;
import com.jivejong.springfieldtalentpipeline.config.GeminiProperties;
import com.jivejong.springfieldtalentpipeline.requisition.Requisition;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;
import org.springframework.stereotype.Component;

/**
 * Builds the mock interview call - docs/AI_FEATURES.md Feature 2.
 *
 * <p>One call produces the entire interview: questions, in-character answers, and a closing
 * assessment. This is a deliberate scope decision, not an oversight to "fix" later - a 5-8 turn
 * live chat would resend the whole history every turn and cost tens of thousands of cumulative
 * tokens to cover the same ground.
 *
 * <p>Uses the larger model, because this is the feature where response quality and personality are
 * the point rather than a nicety.
 */
@Component
public class MockInterviewGenerator {

    /** Enough personality to steer voice without crowding out the role context. */
    private static final int MAX_PHRASES = 8;

    /**
     * Free of per-candidate detail so it stays byte-identical across calls, which is what makes it
     * eligible for Gemini's prompt caching.
     */
    private static final String SYSTEM_PROMPT =
            """
            You are simulating a job interview with a fictional character for an applicant tracking \
            system demo. You write BOTH sides: the interviewer's questions and the candidate's \
            answers.

            Treat the character's in-universe occupation and traits as real professional history. \
            Never break the fourth wall - no winking at the audience, no mentioning that anyone is \
            fictional or from a television show.

            Produce between 5 and 8 turns. For each turn:

            - question: what a competent interviewer would actually ask for THIS role. Ground the \
            questions in the role's title, department and target keywords. They should be \
            questions you could not simply reuse for a different job.
            - answer: the candidate's reply, in their voice. This is where the character has to be \
            unmistakable. Two different candidates answering the same question must not be \
            interchangeable - let their temperament, competence, self-awareness and priorities show \
            through in what they choose to talk about, what they dodge, how confident they are, and \
            how they speak. A candidate who is wrong for the role should reveal that through their \
            answers rather than being described as unsuitable.

            On catchphrases: use them as evidence of temperament, and echo one only where it falls \
            naturally into a sentence. Never hand back a bare catchphrase as an entire answer, and \
            never use more than one or two across the whole interview. A transcript that reads like \
            a list of quotes is a failure.

            Then close with:

            - overallAssessment: two or three sentences on how the candidate actually performed in \
            THIS interview - what they demonstrated, what they avoided, where they were weak. This \
            is your read on their performance, not a summary of their resume and not a hiring \
            decision.
            - overallRating: an integer 1-5 for interview performance against this role, using \
            these bands and no other scale:
                5 - answered convincingly throughout; clear evidence they can do this job.
                4 - solid, with one or two thin spots.
                3 - mixed; some real substance, some evasion or gaps.
                2 - largely unconvincing; little relevant substance.
                1 - unable to engage with the role at all.
              Rate the interview, not likeability. Where a candidate sits between two bands, choose \
            the lower one.
            """;

    private final GeminiClient geminiClient;
    private final GeminiProperties properties;

    public MockInterviewGenerator(GeminiClient geminiClient, GeminiProperties properties) {
        this.geminiClient = geminiClient;
        this.properties = properties;
    }

    public record InterviewTurn(Integer questionNumber, String question, String answer) {}

    public record InterviewGeneration(
            List<InterviewTurn> turns, String overallAssessment, Integer overallRating) {}

    public GeminiClient.StructuredResult<InterviewGeneration> generate(
            Candidate candidate, Requisition requisition) {
        return geminiClient.completeStructured(
                properties.getModels().getInterview(),
                SYSTEM_PROMPT,
                userPrompt(candidate, requisition),
                "mock_interview",
                schema(),
                InterviewGeneration.class,
                properties.getTemperature().getInterview());
    }

    private static String userPrompt(Candidate candidate, Requisition requisition) {
        StringBuilder prompt = new StringBuilder();
        prompt.append("CANDIDATE\n");
        prompt.append("Name: ").append(candidate.getName()).append('\n');
        prompt.append("Occupation: ").append(valueOrUnknown(candidate.getOccupation())).append('\n');
        prompt.append("Age: ")
                .append(candidate.getAge() == null ? "not on record" : candidate.getAge())
                .append('\n');
        List<String> phrases = candidate.getPhrases();
        if (phrases != null && !phrases.isEmpty()) {
            prompt.append("Things this person is known to say: ")
                    .append(phrases.stream()
                            .limit(MAX_PHRASES)
                            .map(phrase -> '"' + phrase.replace('"', '\'') + '"')
                            .collect(Collectors.joining(", ")))
                    .append('\n');
        } else {
            // 77% of the pool has no phrases; say so rather than letting the model assume it was
            // simply omitted and invent some.
            prompt.append("Things this person is known to say: nothing on record - build their "
                    + "voice from their occupation alone.\n");
        }

        prompt.append("\nROLE THEY ARE INTERVIEWING FOR\n");
        prompt.append("Title: ").append(requisition.getTitle()).append('\n');
        prompt.append("Department: ").append(valueOrUnknown(requisition.getDepartment())).append('\n');
        prompt.append("Target keywords: ").append(requisition.getTargetKeywords()).append('\n');
        return prompt.toString();
    }

    private static String valueOrUnknown(String value) {
        return value == null || value.isBlank() ? "not on record" : value;
    }

    private static Map<String, Object> schema() {
        Map<String, Object> turnProperties = new LinkedHashMap<>();
        turnProperties.put(
                "questionNumber", Map.of("type", "integer", "description", "1-based, sequential"));
        turnProperties.put("question", Map.of("type", "string"));
        turnProperties.put("answer", Map.of("type", "string"));

        Map<String, Object> turn = new LinkedHashMap<>();
        turn.put("type", "object");
        turn.put("properties", turnProperties);
        turn.put("required", List.of("questionNumber", "question", "answer"));
        turn.put("additionalProperties", false);

        Map<String, Object> turns = new LinkedHashMap<>();
        turns.put("type", "array");
        turns.put("minItems", 5);
        turns.put("maxItems", 8);
        turns.put("items", turn);

        Map<String, Object> rating = new LinkedHashMap<>();
        rating.put("type", "integer");
        rating.put("minimum", 1);
        rating.put("maximum", 5);

        Map<String, Object> properties = new LinkedHashMap<>();
        properties.put("turns", turns);
        properties.put("overallAssessment", Map.of("type", "string"));
        properties.put("overallRating", rating);

        Map<String, Object> schema = new LinkedHashMap<>();
        schema.put("type", "object");
        schema.put("properties", properties);
        schema.put("required", List.of("turns", "overallAssessment", "overallRating"));
        schema.put("additionalProperties", false);
        return schema;
    }
}
