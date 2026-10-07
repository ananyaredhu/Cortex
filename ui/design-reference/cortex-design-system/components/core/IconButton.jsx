import React from 'react';
import { Icon } from './Icon.jsx';
export function IconButton({ icon, label, size = 'md', variant = 'ghost', active, onClick, disabled, style }) {
  const [hover, setHover] = React.useState(false);
  const d = { sm: 28, md: 36, lg: 44 }[size], ic = { sm: 14, md: 16, lg: 20 }[size];
  const bg = active ? 'var(--accent-soft)' : variant === 'secondary' ? 'var(--surface-card)' : 'transparent';
  return (
    <button type="button" aria-label={label} title={label} onClick={onClick} disabled={disabled}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      style={{ width: d, height: d, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', padding: 0,
        borderRadius: 'var(--radius-sm)', border: variant === 'secondary' ? '1px solid var(--border-subtle)' : '1px solid transparent',
        background: hover && !active ? 'color-mix(in srgb, var(--text-strong) 6%, ' + (variant === 'secondary' ? 'var(--surface-card)' : 'transparent') + ')' : bg,
        color: active ? 'var(--accent-text)' : 'var(--text-strong)', cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.4 : 1,
        transition: 'background var(--dur-fast) var(--ease-out)', ...style }}>
      <Icon name={icon} size={ic} />
    </button>
  );
}