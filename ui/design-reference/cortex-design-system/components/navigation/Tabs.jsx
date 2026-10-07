import React from 'react';
export function Tabs({ tabs = [], value, defaultValue, onChange, variant = 'underline' }) {
  const [inner, setInner] = React.useState(defaultValue ?? (tabs[0] && (tabs[0].value ?? tabs[0])));
  const cur = value ?? inner;
  const seg = variant === 'segmented';
  return (
    <div role="tablist" style={{ display: 'flex', gap: seg ? 2 : 20, padding: seg ? 3 : 0, background: seg ? 'var(--bg-sunken)' : 'transparent',
      borderRadius: seg ? 'var(--radius-sm)' : 0, borderBottom: seg ? 'none' : '1px solid var(--border-subtle)', width: seg ? 'fit-content' : undefined }}>
      {tabs.map(t => {
        const v = t.value ?? t, l = t.label ?? t, on = v === cur;
        return (
          <button key={v} role="tab" aria-selected={on} onClick={() => { setInner(v); onChange && onChange(v); }}
            style={{ border: 0, cursor: 'pointer', font: `${on ? 700 : 400} 14px/1 var(--font-sans)`, color: on ? 'var(--text-strong)' : 'var(--text-muted)',
              background: seg && on ? 'var(--surface-card)' : 'transparent', boxShadow: seg && on ? 'var(--shadow-1)' : 'none',
              padding: seg ? '7px 12px' : '10px 0', borderRadius: seg ? 7 : 0, marginBottom: seg ? 0 : -1,
              borderBottom: seg ? 0 : `2px solid ${on ? 'var(--orange)' : 'transparent'}`, display: 'inline-flex', gap: 6, alignItems: 'center' }}>
            {l}{t.count != null && <span style={{ font: '600 11px/1 var(--font-mono)', color: 'var(--text-muted)' }}>{t.count}</span>}
          </button>
        );
      })}
    </div>
  );
}