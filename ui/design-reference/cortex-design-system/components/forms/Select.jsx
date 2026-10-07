import React from 'react';
import { Icon } from '../core/Icon.jsx';
export function Select({ label, options = [], value, defaultValue, onChange, size = 'md', disabled, style }) {
  const h = { sm: 28, md: 36, lg: 44 }[size];
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 6, ...style }}>
      {label && <span style={{ font: '700 13px/1.2 var(--font-sans)', color: 'var(--text-strong)' }}>{label}</span>}
      <span style={{ position: 'relative', display: 'flex' }}>
        <select value={value} defaultValue={defaultValue} onChange={onChange} disabled={disabled}
          style={{ appearance: 'none', WebkitAppearance: 'none', width: '100%', height: h, padding: '0 34px 0 12px', borderRadius: 'var(--radius-sm)',
            border: '1px solid var(--border-subtle)', background: 'var(--surface-card)', color: 'var(--text-strong)', font: '400 14px/1 var(--font-sans)', cursor: 'pointer' }}>
          {options.map(o => typeof o === 'string' ? <option key={o}>{o}</option> : <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <span style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none', color: 'var(--text-muted)' }}><Icon name="chevron-down" size={16} /></span>
      </span>
    </label>
  );
}