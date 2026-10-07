import React from 'react';
import { Icon } from './Icon.jsx';
const SIZES = { sm: { h: 28, px: 10, fs: 13, gap: 6, ic: 14 }, md: { h: 36, px: 14, fs: 14, gap: 8, ic: 16 }, lg: { h: 44, px: 18, fs: 15, gap: 8, ic: 18 } };
const VARIANTS = {
  primary:   { bg: 'var(--ink)', fg: 'var(--paper)', bd: 'var(--ink)', hbg: 'var(--ink-2)' },
  accent:    { bg: 'var(--orange)', fg: 'var(--ink)', bd: 'var(--orange)', hbg: 'var(--orange-600)' },
  secondary: { bg: 'var(--surface-card)', fg: 'var(--text-strong)', bd: 'var(--border-subtle)', hbg: 'var(--bg-app)' },
  ghost:     { bg: 'transparent', fg: 'var(--text-strong)', bd: 'transparent', hbg: 'color-mix(in srgb, var(--text-strong) 6%, transparent)' },
  danger:    { bg: 'var(--surface-card)', fg: 'var(--danger)', bd: 'var(--border-subtle)', hbg: 'var(--danger-soft)' },
};
export function Button({ variant = 'primary', size = 'md', icon, iconRight, fullWidth, disabled, children, onClick, type = 'button', style }) {
  const [hover, setHover] = React.useState(false);
  const [down, setDown] = React.useState(false);
  const s = SIZES[size], v = VARIANTS[variant];
  return (
    <button type={type} disabled={disabled} onClick={onClick}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => { setHover(false); setDown(false); }}
      onMouseDown={() => setDown(true)} onMouseUp={() => setDown(false)}
      style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: s.gap,
        height: s.h, padding: `0 ${s.px}px`, width: fullWidth ? '100%' : undefined,
        font: `700 ${s.fs}px/1 var(--font-sans)`, letterSpacing: '-0.005em', whiteSpace: 'nowrap',
        color: v.fg, background: hover && !disabled ? v.hbg : v.bg, border: `1px solid ${v.bd}`,
        borderRadius: 'var(--radius-sm)', cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.4 : 1,
        transform: down && !disabled ? 'translateY(1px)' : 'none',
        transition: 'background var(--dur-fast) var(--ease-out), transform var(--dur-fast)', ...style,
      }}>
      {icon && <Icon name={icon} size={s.ic} />}
      {children}
      {iconRight && <Icon name={iconRight} size={s.ic} />}
    </button>
  );
}