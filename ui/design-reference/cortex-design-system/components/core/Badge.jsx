import React from 'react';
const TONES = {
  neutral: ['var(--gray-100)', 'var(--text-body)', 'var(--gray-400)'],
  accent: ['var(--accent-soft)', 'var(--accent-text)', 'var(--orange)'],
  success: ['var(--success-soft)', 'var(--success)', 'var(--success)'],
  danger: ['var(--danger-soft)', 'var(--danger)', 'var(--danger)'],
  info: ['var(--info-soft)', 'var(--info)', 'var(--info)'],
  warning: ['var(--warning-soft)', 'oklch(0.48 0.1 70)', 'var(--warning)'],
  inverse: ['var(--ink)', 'var(--paper)', 'var(--orange)'],
};
export function Badge({ tone = 'neutral', dot, children, style }) {
  const [bg, fg, dc] = TONES[tone];
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, height: 22, padding: '0 8px', borderRadius: 'var(--radius-xs)',
      background: bg, color: fg, font: '700 12px/1 var(--font-sans)', whiteSpace: 'nowrap', ...style }}>
      {dot && <span style={{ width: 6, height: 6, borderRadius: '50%', background: dc }} />}
      {children}
    </span>
  );
}