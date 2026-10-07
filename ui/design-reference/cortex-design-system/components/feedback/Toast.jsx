import React from 'react';
import { Icon } from '../core/Icon.jsx';
export function Toast({ message, icon = 'check', tone = 'default', action, onAction, onClose }) {
  const ic = { default: 'var(--orange)', success: 'oklch(0.75 0.13 150)', danger: 'oklch(0.72 0.15 28)' }[tone];
  return (
    <div role="status" style={{ display: 'inline-flex', alignItems: 'center', gap: 12, minHeight: 44, padding: '0 8px 0 14px', borderRadius: 'var(--radius-md)',
      background: 'var(--ink)', color: 'var(--paper)', boxShadow: 'var(--shadow-2)', font: '400 14px/1.3 var(--font-sans)', maxWidth: 420 }}>
      <Icon name={icon} size={16} color={ic} />
      <span style={{ flex: 1, padding: '12px 0' }}>{message}</span>
      {action && <button onClick={onAction} style={{ border: 0, background: 'transparent', color: 'var(--orange)', font: '700 14px/1 var(--font-sans)', cursor: 'pointer', padding: '8px 6px' }}>{action}</button>}
      {onClose && <button aria-label="Dismiss" onClick={onClose} style={{ border: 0, background: 'transparent', color: 'var(--gray-400)', cursor: 'pointer', padding: 6, display: 'flex' }}><Icon name="x" size={14} /></button>}
    </div>
  );
}