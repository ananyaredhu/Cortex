import React from 'react';
import { Icon } from './Icon.jsx';
export function SourcePill({ label, icon, size = 'md', variant = 'outline', index, onClick, style }) {
  const [hover, setHover] = React.useState(false);
  const h = { sm: 24, md: 30, lg: 36 }[size], fs = { sm: 11, md: 12, lg: 13 }[size];
  const outline = variant === 'outline';
  return (
    <span role={onClick ? 'button' : undefined} onClick={onClick}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      style={{ display: 'inline-flex', alignItems: 'center', gap: 7, height: h, padding: `0 ${size === 'sm' ? 9 : 12}px`,
        borderRadius: 'var(--radius-pill)', border: outline ? `${size === 'sm' ? 1.5 : 2}px solid var(--border-strong)` : '1px solid var(--border-subtle)',
        background: hover && onClick ? 'var(--orange-50)' : 'var(--surface-card)', color: 'var(--text-strong)',
        font: `600 ${fs}px/1 var(--font-mono)`, whiteSpace: 'nowrap', cursor: onClick ? 'pointer' : 'default',
        transition: 'background var(--dur-fast) var(--ease-out)', ...style }}>
      {index != null
        ? <span style={{ minWidth: 14, height: 14, borderRadius: 4, background: 'var(--orange)', color: 'var(--ink)', font: '700 10px/14px var(--font-mono)', textAlign: 'center' }}>{index}</span>
        : icon ? <Icon name={icon} size={fs + 2} /> : <span style={{ width: 8, height: 8, borderRadius: 2, background: 'var(--orange)' }} />}
      {label}
    </span>
  );
}