package com.jivejong.springfieldtalentpipeline.web;

import com.jivejong.springfieldtalentpipeline.requisition.Requisition;
import com.jivejong.springfieldtalentpipeline.requisition.RequisitionMatchingService;
import com.jivejong.springfieldtalentpipeline.requisition.RequisitionService;
import com.jivejong.springfieldtalentpipeline.requisition.RequisitionStatus;
import java.time.LocalDate;
import java.util.List;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/requisitions")
public class RequisitionController {

    private final RequisitionService requisitions;
    private final RequisitionMatchingService matching;

    public RequisitionController(
            RequisitionService requisitions, RequisitionMatchingService matching) {
        this.requisitions = requisitions;
        this.matching = matching;
    }

    public record CreateRequisitionRequest(
            String title,
            String department,
            String targetKeywords,
            String hiringManager,
            LocalDate openedDate) {}

    /** Every field optional: null means "leave this as it is". */
    public record UpdateRequisitionRequest(
            String title,
            String department,
            String targetKeywords,
            String hiringManager,
            RequisitionStatus status) {}

    public record RequisitionResponse(
            UUID id,
            String title,
            String department,
            String targetKeywords,
            String hiringManager,
            RequisitionStatus status,
            LocalDate openedDate) {

        static RequisitionResponse of(Requisition requisition) {
            return new RequisitionResponse(
                    requisition.getId(),
                    requisition.getTitle(),
                    requisition.getDepartment(),
                    requisition.getTargetKeywords(),
                    requisition.getHiringManager(),
                    requisition.getStatus(),
                    requisition.getOpenedDate());
        }
    }

    public record MatchResponse(
            UUID candidateId,
            Integer externalId,
            String name,
            String occupation,
            String characterStatus,
            double rank) {}

    @PostMapping
    public ResponseEntity<RequisitionResponse> create(@RequestBody CreateRequisitionRequest request) {
        Requisition saved = requisitions.create(
                request.title(),
                request.department(),
                request.targetKeywords(),
                request.hiringManager(),
                request.openedDate());
        return ResponseEntity.status(HttpStatus.CREATED).body(RequisitionResponse.of(saved));
    }

    @GetMapping("/{id}")
    public RequisitionResponse get(@PathVariable UUID id) {
        return RequisitionResponse.of(requisitions.get(id));
    }

    /**
     * Partial update - omitted fields are left alone.
     *
     * <p>Changing {@code targetKeywords} is what makes cached AI profiles for this requisition
     * stale: they were scored against the previous role description, so the next
     * {@code POST /api/applications/{id}/ai-profile} regenerates rather than serving cache.
     * Submitting the same keywords again is a no-op and does not trigger that.
     */
    @PatchMapping("/{id}")
    public RequisitionResponse update(
            @PathVariable UUID id, @RequestBody UpdateRequisitionRequest request) {
        return RequisitionResponse.of(requisitions.update(
                id,
                request.title(),
                request.department(),
                request.targetKeywords(),
                request.hiringManager(),
                request.status()));
    }

    /** Full-text-search-ranked candidate matches against the requisition's target keywords. */
    @GetMapping("/{id}/matches")
    public List<MatchResponse> matches(
            @PathVariable UUID id,
            @RequestParam(defaultValue = "20") int limit) {
        return matching.findMatches(id, limit).stream()
                .map(match -> new MatchResponse(
                        match.getId(),
                        match.getExternalId(),
                        match.getName(),
                        match.getOccupation(),
                        match.getCharacterStatus(),
                        match.getRank() == null ? 0d : match.getRank()))
                .toList();
    }
}
