package com.jivejong.springfieldtalentpipeline.requisition;

import java.util.List;
import java.util.NoSuchElementException;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class RequisitionMatchingService {

    public static final int DEFAULT_MAX_RESULTS = 20;

    private final RequisitionRepository requisitions;
    private final CandidateMatchRepository matches;

    public RequisitionMatchingService(
            RequisitionRepository requisitions, CandidateMatchRepository matches) {
        this.requisitions = requisitions;
        this.matches = matches;
    }

    @Transactional(readOnly = true)
    public List<CandidateMatch> findMatches(UUID requisitionId, int maxResults) {
        Requisition requisition = requisitions
                .findById(requisitionId)
                .orElseThrow(() -> new NoSuchElementException("No requisition " + requisitionId));
        String keywords = requisition.getTargetKeywords();
        if (keywords == null || keywords.isBlank()) {
            return List.of();
        }
        return matches.findMatches(keywords, Math.max(1, maxResults));
    }
}
