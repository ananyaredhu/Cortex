import React from 'react';
export function Avatar({ src, name, size = 32, ring, shape = 'circle', style }) {
  const initials = (name || '?').split(' ').map(w => w[0]).slice(0, 2).join('').toUpperCase();
  return (
    <span title={name} style={{ width: size, height: size, flexShrink: 0, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      borderRadius: shape === 'circle' ? '50%' : Math.round(size * 0.28), background: 'var(--avatar-tile)', overflow: 'hidden',
      boxShadow: ring ? `inset 0 0 0 ${size >= 48 ? 2 : 1.5}px var(--ink)` : 'inset 0 0 0 1px var(--border-subtle)',
      font: `700 ${Math.round(size * 0.38)}px/1 var(--font-sans)`, color: 'var(--ink)', ...style }}>
      {src ? <img src={src} alt={name || ''} style={{ width: '88%', height: '88%', display: 'block' }} /> : initials}
    </span>
  );
}