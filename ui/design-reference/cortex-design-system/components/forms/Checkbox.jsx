import React from 'react';
import { Icon } from '../core/Icon.jsx';
export function Checkbox({ label, description, checked, defaultChecked, onChange, disabled }) {
  const [inner, setInner] = React.useState(!!defaultChecked);
  const on = checked ?? inner;
  const toggle = () => { if (disabled) return; setInner(!on); onChange && onChange(!on); };
  return (
    <span onClick={toggle} role="checkbox" aria-checked={on} style={{ display: 'inline-flex', gap: 10, alignItems: 'flex-start', cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.4 : 1 }}>
      <span style={{ width: 18, height: 18, marginTop: 1, flexShrink: 0, borderRadius: 5, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        background: on ? 'var(--ink)' : 'var(--surface-card)', border: `1.5px solid ${on ? 'var(--ink)' : 'var(--gray-400)'}`, color: 'var(--paper)', transition: 'background var(--dur-fast)' }}>
        {on && <Icon name="check" size={13} strokeWidth={3} />}
      </span>
      {(label || description) && <span style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        {label && <span style={{ font: '400 14px/1.35 var(--font-sans)', color: 'var(--text-strong)' }}>{label}</span>}
        {description && <span style={{ font: '400 12px/1.4 var(--font-sans)', color: 'var(--text-muted)' }}>{description}</span>}
      </span>}
    </span>
  );
}