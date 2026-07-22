import React, { useState, useMemo } from 'react';
import { ChevronDown, ChevronUp, XIcon } from './Icons';

/**
 * TagPanel — categorized multi-row tag filter.
 * Props:
 *   tags        — array of { id, name, category, song_count }
 *   activeTags  — Set of active tag names
 *   onToggle    — (tagName) => void
 *   onClearAll  — () => void
 */
export default function TagPanel({ tags = [], activeTags = new Set(), onToggle, onClearAll }) {
  // Group tags by category; uncategorized → "Other"
  const grouped = useMemo(() => {
    const map = {};
    for (const tag of tags) {
      const cat = tag.category || 'Other';
      if (!map[cat]) map[cat] = [];
      map[cat].push(tag);
    }
    // Sort categories: known ones first, Other last
    const ORDER = ['Genre','Era','Feel','Tempo','Vibe'];
    return Object.entries(map).sort(([a], [b]) => {
      const ai = ORDER.indexOf(a), bi = ORDER.indexOf(b);
      if (ai === -1 && bi === -1) return a.localeCompare(b);
      if (ai === -1) return 1;
      if (bi === -1) return -1;
      return ai - bi;
    });
  }, [tags]);

  // Category open/closed state — all open by default
  const [open, setOpen] = useState(() => {
    const s = {};
    (grouped).forEach(([cat]) => { s[cat] = true; });
    return s;
  });

  const toggleCat = (cat) => setOpen(p => ({ ...p, [cat]: !p[cat] }));

  const activeList = [...activeTags];

  return (
    <div className="tag-panel">
      {/* Active tags bar */}
      {activeList.length > 0 && (
        <div className="active-tags-bar">
          {activeList.map(name => (
            <div key={name} className="active-tag">
              {name}
              <button onClick={() => onToggle(name)} aria-label={`Remove ${name}`}>
                <XIcon size={12} />
              </button>
            </div>
          ))}
          <button className="clear-all-btn" onClick={onClearAll}>Clear all</button>
        </div>
      )}

      {/* Categorised tag grid */}
      {grouped.map(([cat, catTags]) => (
        <div key={cat} className="tag-category">
          <div className="cat-header" onClick={() => toggleCat(cat)} role="button" tabIndex={0}>
            <span className="cat-label">{cat}</span>
            <span className="cat-count">{catTags.length}</span>
            {open[cat] ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </div>
          {open[cat] && (
            <div className="tag-grid">
              {catTags
                .sort((a, b) => (b.song_count - a.song_count) || a.name.localeCompare(b.name))
                .map(tag => (
                  <button
                    key={tag.id}
                    className={`tag-chip ${activeTags.has(tag.name) ? 'active' : ''}`}
                    onClick={() => onToggle(tag.name)}
                  >
                    {tag.name}
                    {tag.song_count > 0 && (
                      <span style={{ marginLeft: 4, opacity: 0.5, fontSize: 11 }}>
                        {tag.song_count}
                      </span>
                    )}
                  </button>
                ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
