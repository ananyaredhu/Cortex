import React from 'react';
export function Tooltip({ label, children, side = 'top', open }) {
  const [hover, setHover] = React.useState(false);
  const show = open ?? hover;
  const pos = side === 'top' ? { bottom: 'calc(100% + 6px)' } : { top: 'calc(100% + 6px)' };
  return (
    <span onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)} style={{ position: 'relative', display: 'inline-flex' }}>
      {children}
      {show && <span role="tooltip" style={{ position: 'absolute', left: '50%', transform: 'translateX(-50%)', ...pos, zIndex: 40, whiteSpace: 'nowrap',
        padding: '6px 8px', borderRadius: 'var(--radius-xs)', background: 'var(--ink)', color: 'var(--paper)', font: '400 12px/1.2 var(--font-sans)', pointerEvents: 'none' }}>{label}</span>}
    </span>
  );
}