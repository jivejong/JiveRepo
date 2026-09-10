package com.jivejong.springfieldtalentpipeline.offer;

/**
 * What the candidate did with an offer.
 *
 * <p>Decided arithmetically against reference wage data, not by a model. The whole point of this
 * step is that it is deterministic and explainable: the same offer amount for the same occupation
 * always produces the same outcome and the same rationale.
 */
public enum OfferOutcome {
    ACCEPTED,
    DECLINED
}
