import { useState } from 'react';
import { api } from '../lib/api.js';
import { Status } from './Status.jsx';

/**
 * Step 1. targetKeywords is the field that drives everything downstream - it is the full-text
 * query the matches endpoint runs against candidate occupations, so the placeholder suggests
 * several terms rather than one.
 */
export default function RequisitionForm({ onCreated, disabled }) {
  const [title, setTitle] = useState('Bartender');
  const [department, setDepartment] = useState('Food & Beverage');
  const [targetKeywords, setTargetKeywords] = useState('bartender tavern bar drinks');
  const [hiringManager, setHiringManager] = useState('Marge Simpson');
  const [state, setState] = useState('idle');
  const [error, setError] = useState(null);

  async function onSubmit(event) {
    event.preventDefault();
    setState('loading');
    setError(null);
    try {
      const requisition = await api.createRequisition({
        title: title.trim(),
        department: department.trim(),
        targetKeywords: targetKeywords.trim(),
        hiringManager: hiringManager.trim(),
        openedDate: new Date().toISOString().slice(0, 10),
      });
      setState('idle');
      onCreated(requisition);
    } catch (cause) {
      setError(cause.message);
      setState('error');
    }
  }

  return (
    <section className="panel">
      <h2>1 · Open a requisition</h2>
      <form onSubmit={onSubmit}>
        <label>
          Title
          <input value={title} onChange={(e) => setTitle(e.target.value)} required data-testid="req-title" />
        </label>
        <label>
          Department
          <input
            value={department}
            onChange={(e) => setDepartment(e.target.value)}
            data-testid="req-department"
          />
        </label>
        <label>
          Target keywords <span className="hint">— the full-text query run against occupations</span>
          <input
            value={targetKeywords}
            onChange={(e) => setTargetKeywords(e.target.value)}
            required
            data-testid="req-keywords"
          />
        </label>
        <label>
          Hiring manager
          <input
            value={hiringManager}
            onChange={(e) => setHiringManager(e.target.value)}
            data-testid="req-manager"
          />
        </label>
        <button type="submit" disabled={disabled || state === 'loading'} data-testid="req-submit">
          {state === 'loading' ? 'Creating…' : 'Create requisition & find matches'}
        </button>
      </form>
      <Status state={state} error={error} testId="req" />
    </section>
  );
}
