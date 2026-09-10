import { useState } from 'react';
import { api } from '../lib/api.js';
import { Status } from './Status.jsx';

const money = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 0,
});

/**
 * The offer step. This is the one genuine decision point in the walk to a terminal stage - the
 * three hops before it are mechanical, this one needs a number from a person.
 *
 * One-shot by design: the form disappears once submitted. Both outcomes are terminal, so there is
 * no second attempt on the same application, which is also how a real offer works. Letting someone
 * edit the amount and resubmit would imply a negotiation the state machine cannot represent.
 */
export default function OfferForm({ application, candidate, onDecided }) {
  const [amount, setAmount] = useState('45000');
  const [state, setState] = useState('idle');
  const [error, setError] = useState(null);

  async function submit(event) {
    event.preventDefault();
    const parsed = Number.parseInt(amount, 10);
    if (!Number.isInteger(parsed) || parsed <= 0) {
      setError('Enter a positive annual salary.');
      setState('error');
      return;
    }
    setState('loading');
    setError(null);
    try {
      onDecided(await api.extendOffer(application.id, parsed));
    } catch (cause) {
      setError(cause.message);
      setState('error');
    }
  }

  return (
    <div className="probe" data-testid="offer-form">
      <p className="hint">
        {candidate.name} is at <strong>OFFER</strong>. Enter an annual salary — acceptance is decided
        against national wage data for their occupation, not by a model, so the same number always
        gives the same answer.
      </p>
      <form onSubmit={submit}>
        <div className="row">
          {/*
            No `step`: with type=number the step base is `min`, so step="1000" alongside min="1"
            makes every round salary a step mismatch (1, 1001, 2001, ...). The browser then blocks
            submission silently - the click lands, constraint validation rejects it, and no submit
            event is ever dispatched. The default step of 1 accepts any whole-dollar amount.
          */}
          <input
            type="number"
            min="1"
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
            aria-label="Annual salary offer"
            data-testid="offer-amount"
          />
          <button type="submit" disabled={state === 'loading'} data-testid="offer-submit">
            {state === 'loading' ? 'Extending…' : 'Extend Offer'}
          </button>
        </div>
      </form>
      <p className="hint">
        {amount && Number.parseInt(amount, 10) > 0
          ? `Offering ${money.format(Number.parseInt(amount, 10))} per year. This cannot be changed once sent.`
          : 'This cannot be changed once sent.'}
      </p>
      <Status state={state} error={error} testId="offer" />
    </div>
  );
}

/** The outcome panel, shown for both accepted and declined offers. */
export function OfferOutcome({ offer }) {
  const accepted = offer.decision === 'ACCEPTED';
  return (
    <div
      className={accepted ? 'rejected offer-accepted' : 'rejected'}
      data-testid="offer-outcome"
    >
      <p className="rejected-code" data-testid="offer-decision">
        Offer {money.format(offer.offerAmount)} — {accepted ? 'ACCEPTED' : 'DECLINED'}
      </p>
      <p data-testid="offer-rationale">{offer.decisionRationale}</p>
      <dl>
        <dt>matched occupation</dt>
        <dd data-testid="offer-occupation">
          {offer.matchedOccupationTitle} ({offer.matchedSocCode})
          {offer.fellBackToAggregate && ' — fallback, no confident match'}
        </dd>
        <dt>wage range used</dt>
        <dd data-testid="offer-range">
          {money.format(offer.wageRangeLow)} – {money.format(offer.wageRangeHigh)}
        </dd>
      </dl>
    </div>
  );
}
