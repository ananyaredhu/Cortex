import React from 'react';
import { Icon } from '../core/Icon.jsx';
export function Input({ label, hint, error, icon, size = 'md', value, defaultValue, placeholder, onChange, onKeyDown, type = 'text', disabled, style }) {
  const [focus, setFocus] = React.useState(false);
  const h = { sm: 28, md: 36, lg: 44 }[size];
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 6, ...style }}>
      {label && <span style={{ font: '700 13px/1.2 var(--font-sans)', color: 'var(--text-strong)' }}>{label}</span>}
      <span style={{ display: 'flex', alignItems: 'center', gap: 8, height: h, padding: '0 12px', borderRadius: 'var(--radius-sm)',
        background: disabled ? 'var(--bg-app)' : 'var(--surface-card)', color: 'var(--text-muted)',
        border: `1px solid ${error ? 'var(--danger)' : focus ? 'var(--ink)' : 'var(--border-subtle)'}`,
        boxShadow: focus ? 'var(--ring)' : 'none', transition: 'box-shadow var(--dur-fast), border-color var(--dur-fast)' }}>
        {icon && <Icon name={icon} size={16} />}
        <input type={type} value={value} defaultValue={defaultValue} placeholder={placeholder} disabled={disabled}
          onChange={onChange} onKeyDown={onKeyDown} onFocus={() => setFocus(true)} onBlur={() => setFocus(false)}
          style={{ flex: 1, minWidth: 0, border: 0, outline: 0, background: 'transparent', font: `400 ${size === 'lg' ? 15 : 14}px/1 var(--font-sans)`, color: 'var(--text-strong)' }} />
      </span>
      {(hint || error) && <span style={{ font: '400 12px/1.4 var(--font-sans)', color: error ? 'var(--danger)' : 'var(--text-muted)' }}>{error || hint}</span>}
    </label>
  );
}