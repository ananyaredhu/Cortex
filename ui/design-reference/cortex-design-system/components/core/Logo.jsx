import React from 'react';
export function Logo({ size = 24, variant = 'full', onDark, markSrc = 'assets/logo/cortex-mark.svg', style }) {
  const mark = onDark
    ? <span style={{ width: size * 1.25, height: size * 1.25, borderRadius: size * 0.32, background: '#fff', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}><img src={markSrc} alt="" style={{ width: size, height: size }} /></span>
    : <img src={markSrc} alt="" style={{ width: size * 1.15, height: size * 1.15 }} />;
  return (
    <span aria-label="cortex" style={{ display: 'inline-flex', alignItems: 'center', gap: size * 0.3, color: onDark ? 'var(--paper)' : 'var(--ink)', ...style }}>
      {mark}
      {variant === 'full' && <span style={{ font: `600 ${size}px/1 var(--font-serif)`, letterSpacing: '-0.02em' }}>cortex</span>}
    </span>
  );
}