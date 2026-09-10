package com.jivejong.springfieldtalentpipeline.requisition;

import java.time.Instant;
import java.time.LocalDate;
import java.util.NoSuchElementException;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Requisition lifecycle.
 *
 * <p>Editing {@code targetKeywords} is not just a field update: it is what makes every cached AI
 * profile for this requisition stale, because those profiles were scored against the old role
 * description. {@link Requisition#updateTargetKeywords} moves {@code keywordsUpdatedAt} only when
 * the text actually differs, so re-saving an unchanged form does not force a round of regeneration.
 */
@Service
public class RequisitionService {

    private static final Logger log = LoggerFactory.getLogger(RequisitionService.class);

    private final RequisitionRepository requisitions;

    public RequisitionService(RequisitionRepository requisitions) {
        this.requisitions = requisitions;
    }

    @Transactional
    public Requisition create(
            String title,
            String department,
            String targetKeywords,
            String hiringManager,
            LocalDate openedDate) {
        requireText(title, "title");
        requireText(
                targetKeywords,
                "targetKeywords - it is the full-text search query used for matching");
        return requisitions.save(new Requisition(
                title.trim(),
                department,
                targetKeywords.trim(),
                hiringManager,
                openedDate == null ? LocalDate.now() : openedDate,
                Instant.now()));
    }

    @Transactional(readOnly = true)
    public Requisition get(UUID id) {
        return requisitions
                .findById(id)
                .orElseThrow(() -> new NoSuchElementException("No requisition " + id));
    }

    /**
     * Partial update: a null field is left alone, so a caller can change keywords without restating
     * the rest of the requisition. Clearing an optional field is deliberately not expressible - it
     * would make null ambiguous, and nothing here needs it.
     */
    @Transactional
    public Requisition update(
            UUID id,
            String title,
            String department,
            String targetKeywords,
            String hiringManager,
            RequisitionStatus status) {
        Requisition requisition = get(id);

        if (title != null) {
            requireText(title, "title");
            requisition.setTitle(title.trim());
        }
        if (department != null) {
            requisition.setDepartment(department);
        }
        if (hiringManager != null) {
            requisition.setHiringManager(hiringManager);
        }
        if (status != null) {
            requisition.setStatus(status);
        }
        if (targetKeywords != null) {
            requireText(targetKeywords, "targetKeywords");
            String trimmed = targetKeywords.trim();
            boolean changed = !trimmed.equals(requisition.getTargetKeywords());
            requisition.updateTargetKeywords(trimmed, Instant.now());
            if (changed) {
                log.info(
                        "Requisition {} target keywords changed; cached AI profiles for its "
                                + "applications are now stale and will regenerate on next request",
                        id);
            }
        }
        return requisitions.save(requisition);
    }

    private static void requireText(String value, String field) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(field + " is required");
        }
    }
}
