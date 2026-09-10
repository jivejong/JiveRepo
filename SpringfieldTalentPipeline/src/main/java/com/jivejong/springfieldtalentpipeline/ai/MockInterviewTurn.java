package com.jivejong.springfieldtalentpipeline.ai;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
import java.util.UUID;

/** One question-and-answer pair inside a {@link MockInterviewSession}. */
@Entity
@Table(name = "mock_interview_turn")
public class MockInterviewTurn {

    @Id
    @GeneratedValue(strategy = GenerationType.UUID)
    private UUID id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "session_id", nullable = false)
    private MockInterviewSession session;

    /** 1 through 5-8. */
    @Column(name = "question_number", nullable = false)
    private int questionNumber;

    @Column(nullable = false, columnDefinition = "text")
    private String question;

    /** The candidate's in-character response. */
    @Column(nullable = false, columnDefinition = "text")
    private String answer;

    protected MockInterviewTurn() {
        // for JPA
    }

    MockInterviewTurn(
            MockInterviewSession session, int questionNumber, String question, String answer) {
        this.session = session;
        this.questionNumber = questionNumber;
        this.question = question;
        this.answer = answer;
    }

    public UUID getId() {
        return id;
    }

    public MockInterviewSession getSession() {
        return session;
    }

    public int getQuestionNumber() {
        return questionNumber;
    }

    public String getQuestion() {
        return question;
    }

    public String getAnswer() {
        return answer;
    }
}
