package com.jivejong.springfieldtalentpipeline.offer;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

/**
 * One row of national wage reference data, keyed by SOC occupation code.
 *
 * <p>Reference data imported once, not fetched at runtime - the same treatment the candidate pool
 * gets from The Simpsons API. Percentiles are stored as whole dollars because that is the precision
 * the source publishes and the precision the decision needs.
 *
 * <p>{@code AGGREGATE_SOC_CODE} is the source's own "All Occupations" row, used as the fallback when
 * a candidate's free-text occupation cannot be matched with enough confidence.
 */
@Entity
@Table(name = "occupation_wage")
public class OccupationWage {

    /** The source's aggregate row covering every occupation. */
    public static final String AGGREGATE_SOC_CODE = "00-0000";

    @Id
    @Column(name = "soc_code", length = 16)
    private String socCode;

    @Column(name = "occ_title", nullable = false, columnDefinition = "text")
    private String occTitle;

    @Column(name = "pct10")
    private Integer pct10;

    @Column(name = "pct25")
    private Integer pct25;

    @Column(name = "median")
    private Integer median;

    @Column(name = "pct75")
    private Integer pct75;

    @Column(name = "pct90")
    private Integer pct90;

    protected OccupationWage() {
        // for JPA
    }

    public OccupationWage(
            String socCode,
            String occTitle,
            Integer pct10,
            Integer pct25,
            Integer median,
            Integer pct75,
            Integer pct90) {
        this.socCode = socCode;
        this.occTitle = occTitle;
        this.pct10 = pct10;
        this.pct25 = pct25;
        this.median = median;
        this.pct75 = pct75;
        this.pct90 = pct90;
    }

    public boolean isAggregate() {
        return AGGREGATE_SOC_CODE.equals(socCode);
    }

    /** True when this row can actually support a decision - both ends of the range are present. */
    public boolean hasUsableRange() {
        return pct10 != null && pct90 != null;
    }

    public String getSocCode() {
        return socCode;
    }

    public String getOccTitle() {
        return occTitle;
    }

    public Integer getPct10() {
        return pct10;
    }

    public Integer getPct25() {
        return pct25;
    }

    public Integer getMedian() {
        return median;
    }

    public Integer getPct75() {
        return pct75;
    }

    public Integer getPct90() {
        return pct90;
    }
}
