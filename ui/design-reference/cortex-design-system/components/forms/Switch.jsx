import React from 'react';
export function Switch({ checked, defaultChecked, onChange, label, size = 'md', disabled }) {
  const [inner, setInner] = React.useState(!!defaultChecked);
  const on = checked ?? inner;
  const w = size === 'sm' ? 28 : 36, h = size === 'sm' ? 16 : 20, k = h - 4;
  const toggle = () => { if (disabled) return; setInner(!on); onChange && onChange(!on); };
  return (
    <span onClick={toggle} role="switch" aria-checked={on} style={{ display: 'inline-flex', alignItems: 'center', gap: 10, cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.4 : 1 }}>
      <span style={{ width: w, height: h, borderRadius: h, background: on ? 'var(--ink)' : 'var(--gray-200)', position: 'relative', flexShrink: 0, transition: 'background var(--dur-base) var(--ease-out)' }}>
        <span style={{ position: 'absolute', top: 2, left: on ? w - k - 2 : 2, width: k, height: k, borderRadius: '50%', background: on ? 'var(--orange)' : '#fff', boxShadow: '0 1px 2px rgba(20,20,20,.2)', transition: 'left var(--dur-base) var(--ease-out), background var(--dur-base)' }} />
      </span>
      {label && <span style={{ font: '400 14px/1.3 var(--font-sans)', color: 'var(--text-strong)' }}>{label}</span>}
    </span>
  );
}