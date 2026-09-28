/**
 * The button row at the bottom of every page. Back and forward are optional because the first and
 * last pages each lack one; "New requisition" is always present so any page can return to step 1.
 */
export default function PageNav({ back, forward, onRestart }) {
  return (
    <div className="actions page-nav">
      {back && (
        <button type="button" onClick={back.onClick} data-testid="nav-back">
          {back.label}
        </button>
      )}
      {forward && (
        <button
          type="button"
          onClick={forward.onClick}
          disabled={forward.disabled}
          data-testid="nav-forward"
        >
          {forward.label}
        </button>
      )}
      <button type="button" onClick={onRestart} data-testid="restart">
        New requisition
      </button>
    </div>
  );
}
