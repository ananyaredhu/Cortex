import React from 'react';
export function Icon({ name, size = 16, strokeWidth = 2, color = 'currentColor', style }) {
  const L = typeof window !== 'undefined' ? window.lucide : null;
  const key = String(name).replace(/(^|-)(\w)/g, (_, __, c) => c.toUpperCase());
  let node = L && ((L.icons && L.icons[key]) || L[key]);
  if (node && node[0] === 'svg') node = node[2];
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={strokeWidth}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ flexShrink: 0, display: 'block', ...style }}>
      {(Array.isArray(node) ? node : []).map(([t, a], i) => React.createElement(t, { ...a, key: i }))}
    </svg>
  );
}