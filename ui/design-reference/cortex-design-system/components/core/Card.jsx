import React from 'react';
export function Card({ tone = 'card', padding = 20, radius = 'lg', interactive, onClick, children, style }) {
  const [hover, setHover] = React.useState(false);
  const bg = { card: 'var(--surface-card)', sunken: 'var(--bg-sunken)', inverse: 'var(--surface-inverse)', accent: 'var(--accent-soft)' }[tone];
  return (
    <div onClick={onClick} onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      style={{ background: bg, color: tone === 'inverse' ? 'var(--text-on-inverse)' : 'var(--text-strong)', padding,
        borderRadius: `var(--radius-${radius})`, border: tone === 'card' ? '1px solid ' + (hover && interactive ? 'var(--gray-400)' : 'var(--border-subtle)') : '1px solid transparent',
        cursor: interactive ? 'pointer' : undefined, transition: 'border-color var(--dur-fast) var(--ease-out)', ...style }}>
      {children}
    </div>
  );
}