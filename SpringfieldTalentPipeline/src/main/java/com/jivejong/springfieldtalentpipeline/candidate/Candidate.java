package com.jivejong.springfieldtalentpipeline.candidate;

import jakarta.persistence.CollectionTable;
import jakarta.persistence.Column;
import jakarta.persistence.ElementCollection;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.OrderColumn;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.UUID;

/**
 * A candidate in the talent pool, synced from The Simpsons API. See docs/DATA_MODEL.md.
 *
 * <p>{@code externalId} is the API's own character id and carries a unique constraint: it is what
 * makes a re-sync an update rather than a duplicate.
 */
@Entity
@Table(
        name = "candidate",
        uniqueConstraints =
                @UniqueConstraint(name = "uk_candidate_external_id", columnNames = "external_id"))
public class Candidate {

    @Id
    @GeneratedValue(strategy = GenerationType.UUID)
    private UUID id;

    @Column(name = "external_id", nullable = false, unique = true)
    private Integer externalId;

    @Column(nullable = false)
    private String name;

    /**
     * Null for roughly 94% of the pool - most background characters have no age in the source. Not
     * defaulted to 0, which would look like real data (docs/SIMPSONS_API.md).
     */
    @Column private Integer age;

    @Column private String gender;

    /** Free text. This is the field that does the heavy lifting for requisition matching. */
    @Column(columnDefinition = "text")
    private String occupation;

    @Enumerated(EnumType.STRING)
    @Column(name = "character_status", nullable = false, length = 32)
    private CharacterStatus characterStatus = CharacterStatus.UNKNOWN;

    /** Raw {@code portrait_path}; resolve against the CDN base in SimpsonsApiProperties. */
    @Column(name = "portrait_path")
    private String portraitPath;

    @ElementCollection(fetch = FetchType.LAZY)
    @CollectionTable(
            name = "candidate_phrase",
            joinColumns = @JoinColumn(name = "candidate_id", nullable = false))
    @OrderColumn(name = "phrase_order")
    @Column(name = "phrase", columnDefinition = "text")
    private List<String> phrases = new ArrayList<>();

    @Column(name = "imported_at", nullable = false, updatable = false)
    private Instant importedAt;

    @Column(name = "last_synced_at", nullable = false)
    private Instant lastSyncedAt;

    protected Candidate() {
        // for JPA
    }

    public Candidate(Integer externalId, String name) {
        this.externalId = Objects.requireNonNull(externalId, "externalId");
        this.name = Objects.requireNonNull(name, "name");
    }

    public UUID getId() {
        return id;
    }

    public Integer getExternalId() {
        return externalId;
    }

    public String getName() {
        return name;
    }

    public void setName(String name) {
        this.name = name;
    }

    public Integer getAge() {
        return age;
    }

    public void setAge(Integer age) {
        this.age = age;
    }

    public String getGender() {
        return gender;
    }

    public void setGender(String gender) {
        this.gender = gender;
    }

    public String getOccupation() {
        return occupation;
    }

    public void setOccupation(String occupation) {
        this.occupation = occupation;
    }

    public CharacterStatus getCharacterStatus() {
        return characterStatus;
    }

    public void setCharacterStatus(CharacterStatus characterStatus) {
        this.characterStatus = characterStatus;
    }

    public String getPortraitPath() {
        return portraitPath;
    }

    public void setPortraitPath(String portraitPath) {
        this.portraitPath = portraitPath;
    }

    public List<String> getPhrases() {
        return phrases;
    }

    public void setPhrases(List<String> phrases) {
        // Mutate in place: Hibernate owns this collection instance.
        this.phrases.clear();
        if (phrases != null) {
            this.phrases.addAll(phrases);
        }
    }

    public Instant getImportedAt() {
        return importedAt;
    }

    public void setImportedAt(Instant importedAt) {
        this.importedAt = importedAt;
    }

    public Instant getLastSyncedAt() {
        return lastSyncedAt;
    }

    public void setLastSyncedAt(Instant lastSyncedAt) {
        this.lastSyncedAt = lastSyncedAt;
    }

    @Override
    public boolean equals(Object other) {
        if (this == other) {
            return true;
        }
        if (!(other instanceof Candidate candidate)) {
            return false;
        }
        return id != null && id.equals(candidate.id);
    }

    @Override
    public int hashCode() {
        return Candidate.class.hashCode();
    }

    @Override
    public String toString() {
        return "Candidate[externalId=%d, name=%s]".formatted(externalId, name);
    }
}
