import { useState, useEffect, useRef } from 'react';
import { api } from '../api/client';
import { T } from '../styles/tokens';
import type { AdoWorkItem } from '../api/types';

interface AdoComboboxProps {
  onSelect: (item: AdoWorkItem) => void;
}

export function AdoCombobox({ onSelect }: AdoComboboxProps) {
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<AdoWorkItem[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!query.trim()) { setItems([]); return; }
    const timer = setTimeout(async () => {
      setLoading(true);
      try {
        const resp = await api.searchAdo(query);
        setItems(resp.items);
        setOpen(true);
      } catch { setItems([]); }
      setLoading(false);
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  function handleSelect(item: AdoWorkItem) {
    setQuery(item.title);
    setOpen(false);
    onSelect(item);
  }

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <input
        value={query}
        onChange={e => setQuery(e.target.value)}
        onFocus={() => items.length > 0 && setOpen(true)}
        placeholder="Search ADO work items (placeholder)..."
        style={{
          width: '100%', padding: '6px 8px', borderRadius: 4,
          border: `1px solid ${T.border}`, background: T.bg1,
          color: T.text0, fontSize: 10, outline: 'none',
        }}
      />
      {loading && (
        <div style={{ position: 'absolute', right: 8, top: 7, fontSize: 8, color: T.text3 }}>...</div>
      )}
      {open && items.length > 0 && (
        <div style={{
          position: 'absolute', top: '100%', left: 0, right: 0,
          marginTop: 2, background: T.bg1, border: `1px solid ${T.border}`,
          borderRadius: 4, maxHeight: 200, overflow: 'auto', zIndex: 10,
          boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
        }}>
          {items.map(item => (
            <div
              key={item.id}
              onClick={() => handleSelect(item)}
              style={{
                padding: '6px 8px', cursor: 'pointer',
                borderBottom: `1px solid ${T.border}`,
              }}
              onMouseEnter={e => (e.currentTarget.style.background = T.bg2)}
              onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                <span style={{ fontSize: 8, color: T.text3, fontFamily: 'monospace' }}>{item.id}</span>
                <span style={{ fontSize: 9, color: T.text0, fontWeight: 500 }}>{item.title}</span>
                <span style={{
                  fontSize: 7, color: T.accent, background: T.accent + '14',
                  border: `1px solid ${T.accent}22`, padding: '0 4px',
                  borderRadius: 2, marginLeft: 'auto',
                }}>{item.type}</span>
              </div>
              <div style={{ fontSize: 7, color: T.text3, marginTop: 1 }}>
                {item.program} · {item.owner} · {item.priority}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
