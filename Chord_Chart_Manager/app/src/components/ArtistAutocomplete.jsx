import React, { useEffect, useState } from 'react';
import * as cache from '../lib/cache';
import { distinctArtists, matchingArtists } from '../lib/artistAutocomplete';

export default function ArtistAutocomplete({ value, onChange }) {
  const [artists, setArtists] = useState([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const options = matchingArtists(artists, value);

  useEffect(() => {
    let live = true;
    const load = async () => {
      try {
        const songs = await cache.getCachedSongs();
        if (live) setArtists(distinctArtists(songs));
      } catch { if (live) setArtists([]); }
    };
    void load();
    const unsubscribe = cache.onCacheChange(load);
    return () => { live = false; unsubscribe(); };
  }, []);

  const choose = artist => {
    onChange(artist);
    setOpen(false);
    setActive(-1);
  };

  const onKeyDown = event => {
    if (event.key === 'ArrowDown' && options.length) {
      event.preventDefault(); setOpen(true); setActive(index => (index + 1) % options.length);
    } else if (event.key === 'ArrowUp' && options.length) {
      event.preventDefault(); setOpen(true); setActive(index => (index <= 0 ? options.length - 1 : index - 1));
    } else if (event.key === 'Enter' && open && active >= 0 && options[active]) {
      event.preventDefault(); choose(options[active]);
    } else if (event.key === 'Escape') {
      setOpen(false); setActive(-1);
    }
  };

  return <div className="artist-autocomplete">
    <input id="song-artist" className="form-input" role="combobox" aria-label="Artist"
      aria-autocomplete="list" aria-expanded={open && options.length > 0}
      aria-controls="artist-suggestions" aria-activedescendant={active >= 0 ? `artist-option-${active}` : undefined}
      autoComplete="off" value={value} onChange={event => { onChange(event.target.value); setOpen(true); setActive(-1); }}
      onFocus={() => setOpen(true)} onKeyDown={onKeyDown} />
    {open && options.length > 0 && <div id="artist-suggestions" className="artist-suggestions" role="listbox" aria-label="Existing artists">
      {options.map((artist, index) => <div key={artist} id={`artist-option-${index}`} role="option"
        aria-selected={active === index} className={`artist-suggestion${active === index ? ' active' : ''}`}
        onPointerDown={event => event.preventDefault()} onClick={() => choose(artist)}>
        {artist}
      </div>)}
    </div>}
  </div>;
}
