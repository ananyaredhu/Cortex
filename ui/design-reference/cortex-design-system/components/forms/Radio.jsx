import React from 'react';
export function Radio({ options = [], value, defaultValue, onChange, name }) {
  const [inner, setInner] = React.useState(defaultValue);
  const cur = value ?? inner;
  return (
    <span role="radiogroup" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {options.map(o => {
        const opt = typeof o === 'string' ? { value: o, label: o } : o, on = cur === opt.value;
        return (
          <span key={opt.value} role="radio" aria-checked={on} onClick={() => { setInner(opt.value); onChange && onChange(opt.value); }}
            style={{ display: 'inline-flex', gap: 10, alignItems: 'flex-start', cursor: 'pointer' }}>
            <span style={{ width: 18, height: 18, marginTop: 1, flexShrink: 0, borderRadius: '50%', border: `1.5px solid ${on ? 'var(--ink)' : 'var(--gray-400)'}`,
              background: 'var(--surface-card)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
              {on && <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--ink)' }} />}
            </span>
            <span style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <span style={{ font: '400 14px/1.35 var(--font-sans)', color: 'var(--text-strong)' }}>{opt.label}</span>
              {opt.description && <span style={{ font: '400 12px/1.4 var(--font-sans)', color: 'var(--text-muted)' }}>{opt.description}</span>}
            </span>
          </span>
        );
      })}
    </span>
  );
}