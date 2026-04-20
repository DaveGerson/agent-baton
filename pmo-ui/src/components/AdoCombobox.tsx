import { useState, useEffect, useRef, useId } from 'react';
import type { KeyboardEvent } from 'react';
import { api } from '../api/client';
import { T, FONTS, SHADOWS } from '../styles/tokens';
import type { AdoWorkItem } from '../api/types';

interface AdoComboboxProps {
  onSelect: (item: AdoWorkItem) => void;
  inputId?: string;
}

export function AdoCombobox({ onSelect, inputId }: AdoComboboxProps) {
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<AdoWorkItem[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [searchError, setSearchError] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const listboxId = useId() + '-ado-results';

  useEffect(() => {
    if (!query.trim()) { setItems([]); setOpen(false); setSearchError(false); return; }
    setSearchError(false);
    const timer = setTimeout(async () => {
      setLoading(true);
      try {
        const resp = await api.searchAdo(query);
        setItems(resp.items);
        setOpen(true);
        setActiveIndex(-1);
      } catch {
        setItems([]);
        setSearchError(true);
        setOpen(true);
      }
      setLoading(false);
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
        setActiveIndex(-1);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  function handleSelect(item: AdoWorkItem) {
    setQuery(item.title);
    setOpen(false);
    setActiveIndex(-1);
    onSelect(item);
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (!open || items.length === 0) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex(i => Math.min(i + 1, items.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex(i => Math.max(i - 1, 0));
    } else if (e.key === 'Enter' && activeIndex >= 0) {
      e.preventDefault();
      handleSelect(items[activeIndex]);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      setOpen(false);
      setActiveIndex(-1);
    }
  }

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <input
        id={inputId}
        role="combobox"
        aria-label="Search ADO work items"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-activedescendant={activeIndex >= 0 ? `ado-item-${items[activeIndex]?.id}` : undefined}
        value={query}
        onChange={e => setQuery(e.target.value)}
        onFocus={() => items.length > 0 && setOpen(true)}
        onKeyDown={handleKeyDown}
        placeholder="Search ADO work items..."
        style={{
          width: '100%',
          padding: '9px 11px',
          borderRadius: 10,
          border: `2px solid ${T.border}`,
          background: T.bg3,
          color: T.text0,
          fontFamily: FONTS.body,
          fontSize: 13,
          fontWeight: 600,
          outline: 'none',
          boxShadow: 'inset 2px 2px 0 0 rgba(0,0,0,.06)',
        }}
      />
      {loading && (
        <div
          aria-live="polite"
          style={{ position: 'absolute', right: 8, top: 10, fontFamily: FONTS.hand, fontSize: 14, color: T.text2 }}
        >
          Searching...
        </div>
      )}
      {open && (searchError || items.length > 0) && (
        <ul
          id={listboxId}
          role="listbox"
          aria-label="ADO work items"
          style={{
            listStyle: 'none',
            padding: 0,
            margin: 0,
            position: 'absolute',
            top: '100%',
            left: 0,
            right: 0,
            marginTop: 2,
            background: T.bg1,
            border: `2px solid ${T.border}`,
            borderRadius: 12,
            maxHeight: 200,
            overflow: 'hidden',
            zIndex: 10,
            boxShadow: SHADOWS.lg,
          }}
        >
          {searchError ? (
            <li
              role="option"
              aria-selected={false}
              aria-disabled="true"
              style={{ padding: '8px 12px', fontFamily: FONTS.body, fontSize: 13, fontWeight: 600, color: T.cherry, background: T.cherrySoft }}
            >
              Search failed — try again
            </li>
          ) : (
            items.map((item, idx) => (
              <li
                key={item.id}
                id={`ado-item-${item.id}`}
                role="option"
                aria-selected={idx === activeIndex}
                onClick={() => handleSelect(item)}
                style={{
                  padding: '8px 12px',
                  cursor: 'pointer',
                  borderBottom: `1px dashed ${T.borderSoft}`,
                  background: idx === activeIndex ? T.butter : 'transparent',
                  borderLeft: idx === activeIndex ? `2px solid ${T.border}` : '2px solid transparent',
                  fontFamily: FONTS.body,
                  fontSize: 13,
                  fontWeight: 600,
                  color: T.text0,
                }}
                onMouseEnter={() => setActiveIndex(idx)}
                onMouseLeave={() => setActiveIndex(-1)}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <span style={{ fontSize: 10, color: T.text2, fontFamily: FONTS.mono }}>{item.id}</span>
                  <span style={{ fontSize: 13, color: T.text0, fontWeight: 600, fontFamily: FONTS.body }}>{item.title}</span>
                  <span style={{
                    fontSize: 9, color: T.accent, background: T.accent + '14',
                    border: `1px solid ${T.accent}22`, padding: '0 4px',
                    borderRadius: 2, marginLeft: 'auto',
                    fontFamily: FONTS.body,
                  }}>{item.type}</span>
                </div>
                <div style={{ fontSize: 10, color: T.text2, marginTop: 1, fontFamily: FONTS.mono }}>
                  {item.program} · {item.owner} · {item.priority}
                </div>
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}
