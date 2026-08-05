import { useState } from 'react';

export function CollapsiblePanel({ title, defaultOpen = true, children, className = '' }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={`collapsible-panel ${open ? 'collapsible-panel-open' : 'collapsible-panel-closed'} ${className}`}>
      <button
        type="button"
        className="collapsible-panel-header"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <span className="collapsible-panel-title">{title}</span>
        <span className="collapsible-panel-chevron">{open ? '▼' : '▶'}</span>
      </button>
      {open && <div className="collapsible-panel-body">{children}</div>}
    </div>
  );
}
